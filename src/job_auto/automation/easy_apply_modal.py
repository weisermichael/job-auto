"""LinkedIn Easy Apply modal handler — page-aware field filling."""

from __future__ import annotations

import asyncio
import random
import re
from enum import Enum, auto
from pathlib import Path
from typing import Any

import yaml
from playwright.async_api import Page
from pydantic import BaseModel, Field

from job_auto.automation.browser import human_move_and_click, human_type
from job_auto.config import config
from job_auto.knowledge_base.store import kb_store
from job_auto.utils.logging import get_logger

logger = get_logger(__name__)

_MODAL_CSS = ".jobs-easy-apply-modal"


class UnansweredQuestionsError(Exception):
    """Raised when required form fields cannot be answered from profile or Q&A cache."""

    def __init__(self, message: str, questions: list[dict], job_url: str = "") -> None:
        super().__init__(message)
        self.questions = questions
        self.job_url = job_url

# ---------------------------------------------------------------------------
# JS helpers — verbatim from scripts/observe_easy_apply.py
# ---------------------------------------------------------------------------

_EXTRACT_FIELDS_JS = """(modal) => {
    function findLabel(el) {
        if (el.id) {
            const lbl = modal.querySelector('label[for="' + el.id + '"]');
            if (lbl) return lbl.textContent.trim();
        }
        const parentLbl = el.closest('label');
        if (parentLbl) return parentLbl.textContent.trim();
        const ariaLabel = el.getAttribute('aria-label');
        if (ariaLabel) return ariaLabel;
        const placeholder = el.getAttribute('placeholder');
        if (placeholder) return placeholder;
        const container = el.closest(
            '[data-test-form-element], [class*="form-builder"], ' +
            '.jobs-easy-apply-form-element, fieldset'
        );
        if (container) {
            const lbl = container.querySelector('label, legend');
            if (lbl) return lbl.textContent.trim();
        }
        return '';
    }

    const fields = [];

    // Text / number / tel / email inputs
    modal.querySelectorAll(
        'input[type="text"], input[type="number"], input[type="tel"], ' +
        'input[type="email"], input:not([type])'
    ).forEach(el => {
        if (el.type === 'hidden') return;
        fields.push({
            type: el.type || 'text',
            label: findLabel(el),
            name: el.name || el.id || '',
            required: el.required,
            current_value: el.value,
        });
    });

    // Textareas
    modal.querySelectorAll('textarea').forEach(el => {
        fields.push({
            type: 'textarea',
            label: findLabel(el),
            name: el.name || el.id || '',
            required: el.required,
            current_value: el.value,
        });
    });

    // Selects
    modal.querySelectorAll('select').forEach(el => {
        const options = Array.from(el.options)
            .map(o => o.text.trim())
            .filter(t => t && !/select an option/i.test(t));
        fields.push({
            type: 'select',
            label: findLabel(el),
            name: el.name || el.id || '',
            options: options,
            required: el.required,
            current_value: el.options[el.selectedIndex]?.text?.trim() || '',
        });
    });

    // Radio groups (keyed by name attribute)
    const radioGroups = {};
    modal.querySelectorAll('input[type="radio"]').forEach(el => {
        const key = el.name || el.closest('fieldset')?.id || el.id;
        if (!radioGroups[key]) radioGroups[key] = { options: [], required: el.required, checked: null };
        const lbl = findLabel(el);
        radioGroups[key].options.push(lbl || el.value);
        if (el.checked) radioGroups[key].checked = lbl || el.value;
    });
    Object.entries(radioGroups).forEach(([key, group]) => {
        const firstEl = modal.querySelector('input[name="' + key + '"]');
        const fieldset = firstEl && firstEl.closest('fieldset');
        const groupLabel = (fieldset && fieldset.querySelector('legend'))
            ? fieldset.querySelector('legend').textContent.trim()
            : key;
        fields.push({
            type: 'radio',
            label: groupLabel,
            name: key,
            options: group.options,
            required: group.required,
            current_value: group.checked || '',
        });
    });

    // Checkboxes
    modal.querySelectorAll('input[type="checkbox"]').forEach(el => {
        fields.push({
            type: 'checkbox',
            label: findLabel(el),
            name: el.name || el.id || '',
            required: el.required,
            current_value: el.checked,
        });
    });

    // File inputs
    modal.querySelectorAll('input[type="file"]').forEach(el => {
        fields.push({
            type: 'file',
            label: findLabel(el),
            name: el.name || el.id || '',
            required: false,
            current_value: '',
        });
    });

    // Per-page section heading (h3 like "Contact info", "Resume", etc.)
    const sectionH3 = modal.querySelector('.artdeco-modal__content h3');
    const modalH2 = modal.querySelector('.artdeco-modal__header h2');
    const page_title = (sectionH3 ? sectionH3.textContent.trim() : '') ||
                       (modalH2 ? modalH2.textContent.trim() : '');

    // Progress indicator
    const progress = modal.querySelector(
        '[aria-label*="step"], .artdeco-completeness-meter-linear, ' +
        '[data-test-progress], .jobs-easy-apply-modal--progress-bar'
    );
    const progress_text = progress
        ? (progress.getAttribute('aria-label') || progress.textContent).trim()
        : '';

    return { fields, page_title, progress: progress_text };
}"""

_SIGNATURE_JS = """(modal) => {
    const heading = modal.querySelector('h2, h3, .artdeco-modal__header h2');
    const fieldCount = modal.querySelectorAll('input, select, textarea').length;
    const title = heading ? heading.textContent.trim() : '';
    const btn = modal.querySelector('footer button.artdeco-button--primary');
    const btnText = btn ? (btn.getAttribute('aria-label') || btn.textContent).trim() : '';
    return title + '|' + fieldCount + '|' + btnText;
}"""


# ---------------------------------------------------------------------------
# Candidate profile
# ---------------------------------------------------------------------------


class ContactInfo(BaseModel):
    first_name: str = ""
    last_name: str = ""
    phone: str = ""
    phone_country_code: str = "United States (+1)"
    email: str = ""


class AddressInfo(BaseModel):
    street: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""


class WorkAuthorization(BaseModel):
    authorized_us: str = "Yes"
    requires_sponsorship: str = "No"


class EducationInfo(BaseModel):
    highest_level: str = "College - Bachelor of Science"


class Preferences(BaseModel):
    desired_salary: str = ""
    willing_background_check: str = "Yes"
    mark_top_choice: bool = False
    follow_company: bool = False


class ProfileUrls(BaseModel):
    linkedin_profile: str = ""


class CandidateProfile(BaseModel):
    contact: ContactInfo = Field(default_factory=ContactInfo)
    address: AddressInfo = Field(default_factory=AddressInfo)
    work_authorization: WorkAuthorization = Field(default_factory=WorkAuthorization)
    education: EducationInfo = Field(default_factory=EducationInfo)
    preferences: Preferences = Field(default_factory=Preferences)
    urls: ProfileUrls = Field(default_factory=ProfileUrls)


def load_profile() -> CandidateProfile:
    path = config.profile_path
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return CandidateProfile.model_validate(data)
    logger.warning("profile_yaml_not_found", path=str(path))
    return CandidateProfile()


# ---------------------------------------------------------------------------
# Page classification
# ---------------------------------------------------------------------------


class PageType(Enum):
    CONTACT = auto()
    RESUME = auto()
    QUESTIONS = auto()
    WORK_AUTH = auto()
    ADDRESS = auto()
    REVIEW = auto()
    TOP_CHOICE = auto()
    CONFIRMATION = auto()
    UNKNOWN = auto()


def classify_page(page_data: dict) -> PageType:
    title = page_data.get("page_title", "").lower()
    fields = page_data.get("fields", [])
    labels = {f["label"].lower() for f in fields}
    types = {f["type"] for f in fields}

    if "review" in title:
        return PageType.REVIEW
    if "contact" in title:
        return PageType.CONTACT
    if "resume" in title:
        return PageType.RESUME
    if "additional" in title or "screening" in title:
        return PageType.QUESTIONS
    if "work authorization" in title or "work eligibility" in title:
        return PageType.WORK_AUTH
    if "address" in title:
        return PageType.ADDRESS
    if not fields:
        return PageType.CONFIRMATION
    if any("phone" in l for l in labels) and any("email" in l for l in labels):
        return PageType.CONTACT
    if "file" in types or any("resume" in l for l in labels):
        return PageType.RESUME
    if any("authorized" in l or "sponsorship" in l for l in labels):
        return PageType.WORK_AUTH
    if any("street" in l or "zip" in l for l in labels):
        return PageType.ADDRESS
    if any("top choice" in l for l in labels) and len(fields) <= 2:
        return PageType.TOP_CHOICE
    return PageType.QUESTIONS


# ---------------------------------------------------------------------------
# Per-type fillers
# ---------------------------------------------------------------------------


async def _fill_contact(page: Page, modal, fields: list[dict], profile: CandidateProfile) -> None:
    for field in fields:
        label = field["label"].lower()
        ftype = field["type"]
        current = field.get("current_value", "")

        if "phone" in label and ftype in ("tel", "text", "number"):
            if not current and profile.contact.phone:
                loc = modal.get_by_label(field["label"], exact=False)
                if await loc.count() > 0:
                    await loc.first.clear()
                    await loc.first.type(profile.contact.phone, delay=random.randint(30, 80))
        elif "country" in label and ftype == "select":
            if not current and profile.contact.phone_country_code:
                loc = modal.get_by_label(field["label"], exact=False)
                if await loc.count() > 0:
                    try:
                        await loc.first.select_option(label=profile.contact.phone_country_code)
                    except Exception:
                        pass


async def _fill_address(page: Page, modal, fields: list[dict], profile: CandidateProfile) -> None:
    mapping = {
        "street": profile.address.street,
        "city": profile.address.city,
        "state": profile.address.state,
        "zip": profile.address.zip,
        "postal": profile.address.zip,
    }
    for field in fields:
        label_lower = field["label"].lower()
        current = field.get("current_value", "")
        if current:
            continue
        for keyword, value in mapping.items():
            if keyword in label_lower and value:
                loc = modal.get_by_label(field["label"], exact=False)
                if await loc.count() > 0:
                    ftype = field["type"]
                    if ftype == "select":
                        try:
                            await loc.first.select_option(label=value)
                        except Exception:
                            pass
                    else:
                        await loc.first.clear()
                        await loc.first.type(value, delay=random.randint(30, 80))
                break


async def _fill_resume(page: Page, modal, fields: list[dict], profile: CandidateProfile, context: dict) -> None:
    for field in fields:
        ftype = field["type"]
        if ftype == "radio":
            # Radio group of saved resumes — pick the first option
            opts = field.get("options", [])
            if opts and not field.get("current_value"):
                name = field["name"]
                label_text = opts[0]
                radio_loc = modal.locator(f"input[name='{name}']").first
                if await radio_loc.count() > 0:
                    parent_label = modal.locator(f"label", has_text=label_text).first
                    if await parent_label.count() > 0:
                        await parent_label.click()
        elif ftype == "file":
            resume_path = context.get("resume_path", "")
            if resume_path and Path(resume_path).exists():
                file_loc = modal.locator("input[type='file']").first
                if await file_loc.count() > 0:
                    await file_loc.set_input_files(resume_path)
                    await asyncio.sleep(1.5)


async def _fill_work_auth(page: Page, modal, fields: list[dict], profile: CandidateProfile) -> None:
    auth_map = {
        "authorized": profile.work_authorization.authorized_us,
        "sponsorship": profile.work_authorization.requires_sponsorship,
        "sponsor": profile.work_authorization.requires_sponsorship,
    }
    for field in fields:
        label_lower = field["label"].lower()
        current = field.get("current_value", "")
        if current:
            continue
        for keyword, answer in auth_map.items():
            if keyword in label_lower and answer:
                ftype = field["type"]
                if ftype == "radio":
                    opts = field.get("options", [])
                    target = next((o for o in opts if o.lower().startswith(answer.lower())), None)
                    if target:
                        name = field["name"]
                        parent_label = modal.locator(f"label", has_text=target).first
                        if await parent_label.count() > 0:
                            await parent_label.click()
                elif ftype == "select":
                    loc = modal.get_by_label(field["label"], exact=False)
                    if await loc.count() > 0:
                        try:
                            await loc.first.select_option(label=answer)
                        except Exception:
                            pass
                break


async def _fill_questions(
    page: Page,
    modal,
    fields: list[dict],
    profile: CandidateProfile,
    qa_cache: dict[str, str],
) -> tuple[dict[str, str], list[dict]]:
    """Fill question fields.

    Returns:
        (new_answers, unanswered) where new_answers is a dict of newly derived
        {key: answer} for caching, and unanswered is a list of required fields
        that could not be answered from the profile or cache.
    """
    new_answers: dict[str, str] = {}
    unanswered: list[dict] = []

    for field in fields:
        label = field["label"]
        label_lower = label.lower()
        ftype = field["type"]
        current = field.get("current_value", "")

        # Skip already-filled fields
        if current:
            continue

        cache_key = re.sub(r"\s+", " ", label_lower).strip()
        answer: str | None = qa_cache.get(cache_key)

        if answer is None:
            # Derive answer from profile or patterns
            if re.search(r"years of work experience.*with (.+)", label_lower):
                answer = "0"
            elif "linkedin profile" in label_lower:
                answer = profile.urls.linkedin_profile
            elif "desired salary" in label_lower or "expected salary" in label_lower:
                answer = profile.preferences.desired_salary
            elif "education" in label_lower and ftype == "select":
                answer = profile.education.highest_level
            elif "background check" in label_lower:
                answer = profile.preferences.willing_background_check
            elif "authorized" in label_lower and ftype in ("radio", "select"):
                answer = profile.work_authorization.authorized_us
            elif "sponsorship" in label_lower and ftype in ("radio", "select"):
                answer = profile.work_authorization.requires_sponsorship
            else:
                answer = None

        if answer:
            new_answers[cache_key] = answer
            loc = modal.get_by_label(label, exact=False)
            if await loc.count() > 0:
                if ftype == "select":
                    try:
                        await loc.first.select_option(label=answer)
                    except Exception:
                        try:
                            await loc.first.select_option(value=answer)
                        except Exception:
                            pass
                elif ftype == "radio":
                    opts = field.get("options", [])
                    target = next((o for o in opts if o.lower().startswith(answer.lower())), None)
                    if target:
                        parent_label = modal.locator("label", has_text=target).first
                        if await parent_label.count() > 0:
                            await parent_label.click()
                elif ftype == "checkbox":
                    if answer.lower() in ("yes", "true", "1"):
                        if not field.get("current_value"):
                            await loc.first.check()
                else:
                    await loc.first.clear()
                    await loc.first.type(answer, delay=random.randint(30, 80))
        elif field.get("required"):
            # Required field with no answer — record for the user to provide later
            unanswered.append({
                "label": label,
                "type": ftype,
                "options": field.get("options", []),
            })

    return new_answers, unanswered


async def _fill_review(page: Page, modal, fields: list[dict], profile: CandidateProfile) -> None:
    for field in fields:
        ftype = field["type"]
        label_lower = field["label"].lower()
        if ftype == "checkbox":
            if "top choice" in label_lower:
                if profile.preferences.mark_top_choice and not field.get("current_value"):
                    loc = modal.get_by_label(field["label"], exact=False)
                    if await loc.count() > 0:
                        await loc.first.check()
            elif "follow" in label_lower:
                if profile.preferences.follow_company and not field.get("current_value"):
                    loc = modal.get_by_label(field["label"], exact=False)
                    if await loc.count() > 0:
                        await loc.first.check()


async def _fill_top_choice(page: Page, modal, fields: list[dict], profile: CandidateProfile) -> None:
    await _fill_review(page, modal, fields, profile)


async def _fill_page(
    page: Page,
    modal,
    page_data: dict,
    page_type: PageType,
    profile: CandidateProfile,
    context: dict,
    qa_cache: dict[str, str],
) -> tuple[dict[str, str], list[dict]]:
    """Fill one modal page.

    Returns:
        (new_answers, unanswered) where unanswered lists required fields that
        could not be answered (only populated for QUESTIONS / UNKNOWN pages).
    """
    fields = page_data.get("fields", [])
    new_answers: dict[str, str] = {}
    unanswered: list[dict] = []

    if page_type == PageType.CONTACT:
        await _fill_contact(page, modal, fields, profile)
    elif page_type == PageType.ADDRESS:
        await _fill_address(page, modal, fields, profile)
    elif page_type == PageType.RESUME:
        await _fill_resume(page, modal, fields, profile, context)
    elif page_type == PageType.WORK_AUTH:
        await _fill_work_auth(page, modal, fields, profile)
    elif page_type in (PageType.QUESTIONS, PageType.UNKNOWN):
        new_answers, unanswered = await _fill_questions(page, modal, fields, profile, qa_cache)
    elif page_type == PageType.REVIEW:
        await _fill_review(page, modal, fields, profile)
    elif page_type == PageType.TOP_CHOICE:
        await _fill_top_choice(page, modal, fields, profile)
    elif page_type == PageType.CONFIRMATION:
        pass  # Nothing to fill

    return new_answers, unanswered


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


async def handle_easy_apply_modal(
    page: Page,
    context: dict[str, Any],
    profile: CandidateProfile,
) -> None:
    """Drive the LinkedIn Easy Apply modal from first page through submission."""
    modal = page.locator(_MODAL_CSS).first
    qa_cache = kb_store.get_qa_cache("linkedin")
    confirmed = False

    for page_num in range(12):  # max pages
        # Wait for interactive elements to render
        try:
            await page.wait_for_selector(
                f"{_MODAL_CSS} input, {_MODAL_CSS} select, "
                f"{_MODAL_CSS} textarea, {_MODAL_CSS} button",
                timeout=8000,
            )
        except Exception:
            pass  # confirmation or static page

        page_data = await modal.evaluate(_EXTRACT_FIELDS_JS)
        page_type = classify_page(page_data)

        logger.info(
            "easy_apply_modal_page",
            page_num=page_num,
            page_title=page_data.get("page_title", ""),
            page_type=page_type.name,
            field_count=len(page_data.get("fields", [])),
        )

        new_answers, unanswered = await _fill_page(
            page, modal, page_data, page_type, profile, context, qa_cache
        )

        # Persist newly derived Q&A answers
        for key, answer in new_answers.items():
            kb_store.set_qa_answer("linkedin", key, answer)
            qa_cache[key] = answer

        if page_type == PageType.CONFIRMATION:
            logger.info("easy_apply_confirmed")
            confirmed = True
            break

        # If required questions have no answers, record them and abort.
        # The user can add answers to data/profile.yaml or the KB Q&A cache,
        # then re-run to submit the application.
        if unanswered:
            job_url = context.get("job_url", "")
            kb_store.record_pending_questions("linkedin", job_url, unanswered)
            labels = [q["label"] for q in unanswered]
            logger.warning(
                "unanswered_required_questions",
                count=len(unanswered),
                labels=labels,
                job_url=job_url,
            )
            raise UnansweredQuestionsError(
                f"Cannot answer {len(unanswered)} required question(s): {labels}. "
                f"Add answers to data/profile.yaml or the knowledge base Q&A cache "
                f"(storage/knowledge_base.json → linkedin.qa_cache), then re-run.",
                questions=unanswered,
                job_url=job_url,
            )

        # Small human-like pause after filling
        await asyncio.sleep(random.uniform(0.5, 1.0))

        # Check for Submit button
        submit_sel = f"{_MODAL_CSS} button[aria-label='Submit application']"
        submit = modal.locator("button[aria-label='Submit application']")
        if await submit.count() > 0 and await submit.is_visible():
            await asyncio.sleep(random.uniform(0.5, 1.0))
            await human_move_and_click(page, submit_sel)
            await asyncio.sleep(2)
            continue  # capture confirmation page on next iteration

        # Capture signature before clicking Next to detect stuck pages
        sig_before = await modal.evaluate(_SIGNATURE_JS)

        # Click Next / Review
        next_sel = (
            f"{_MODAL_CSS} button[aria-label='Continue to next step'], "
            f"{_MODAL_CSS} button[aria-label='Review your application'], "
            f"{_MODAL_CSS} footer button.artdeco-button--primary"
        )
        try:
            await human_move_and_click(page, next_sel)
        except Exception:
            errors = await modal.locator(".artdeco-inline-feedback--error").all_text_contents()
            if errors:
                raise RuntimeError(f"Form validation failed: {errors}")
            raise

        # Poll up to 6 s for the page signature to change (LinkedIn transitions can be slow)
        sig_after = sig_before
        for _ in range(12):
            await asyncio.sleep(0.5)
            if await page.locator(_MODAL_CSS).count() == 0:
                raise RuntimeError(
                    f"Easy Apply modal closed unexpectedly after Next click "
                    f"(page {page_num}: {page_data.get('page_title', '')})"
                )
            sig_after = await modal.evaluate(_SIGNATURE_JS)
            if sig_after != sig_before:
                break

        if sig_after == sig_before:
            errors = await modal.locator(".artdeco-inline-feedback--error").all_text_contents()
            raise RuntimeError(
                f"Modal page did not advance after Next click "
                f"(page {page_num}: {page_data.get('page_title', '')}). "
                f"Validation errors: {errors or 'none visible'}"
            )

    if not confirmed:
        raise RuntimeError(
            f"Easy Apply modal exited after {page_num + 1} pages without reaching confirmation"
        )
