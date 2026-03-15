# -*- coding: utf-8 -*-
import base64  # kept (you may use it in templates later)

from odoo import http, _
from odoo.http import request

ROLE_MAP = {
    "ap": {"slug": "affiliate", "name": "Affiliate Partner", "desc": "Promote us and earn commission on successful referrals."},
    "lead": {"slug": "lead", "name": "Lead Partner", "desc": "Bring qualified leads; we handle the conversion process."},
    "sales_agent": {"slug": "sales-agent", "name": "Sales Agent", "desc": "Work deals end-to-end and earn commission on sales."},
    "sales_partner": {"slug": "sales-partner", "name": "Sales Partner (Buy–Sell)", "desc": "Resell/buy-sell under partner rules and pricing."},
}
SLUG_TO_ROLE = {v["slug"]: k for k, v in ROLE_MAP.items()}


class PartnerWebsiteController(http.Controller):
    # -----------------------------
    # Website pages
    # -----------------------------
    @http.route("/partners", type="http", auth="public", website=True, sitemap=True)
    def partners_home(self, **kwargs):
        return request.render("partner_attribution_v1.website_partners_home", {"roles": ROLE_MAP})

    @http.route("/partners/<string:role_slug>", type="http", auth="public", website=True, sitemap=True)
    def partners_role_page(self, role_slug, **kwargs):
        role_key = SLUG_TO_ROLE.get(role_slug)
        if not role_key:
            return request.not_found()
        role = ROLE_MAP[role_key]
        return request.render(
            "partner_attribution_v1.website_partner_role_page",
            {"role_key": role_key, "role": role, "roles": ROLE_MAP},
        )

    @http.route("/partners/apply", type="http", auth="public", website=True, sitemap=True)
    def partners_apply(self, role=None, **kwargs):
        role_key = None
        if role:
            if role in ROLE_MAP:
                role_key = role
            elif role in SLUG_TO_ROLE:
                role_key = SLUG_TO_ROLE[role]

        return request.render(
            "partner_attribution_v1.website_partner_apply",
            {"roles": ROLE_MAP, "selected_role": role_key, "post": {}},
        )

    @http.route("/partners/apply/submit", type="http", auth="public", website=True, methods=["POST"], csrf=True)
    def partners_apply_submit(self, **post):
        Inquiry = request.env["partner.attribution.inquiry"].sudo()
        Partner = request.env["res.partner"].sudo()

        partner_role = (post.get("partner_role") or "").strip()
        applicant_name = (post.get("applicant_name") or post.get("name") or "").strip()
        applicant_company = (post.get("applicant_company") or post.get("company") or "").strip()
        email = (post.get("email") or "").strip()
        phone = (post.get("phone") or "").strip()
        note = (post.get("note") or post.get("notes") or "").strip()

        vat = (post.get("vat") or "").strip()
        iban = (post.get("iban") or "").strip()
        coc = (post.get("coc") or "").strip()
        irs = (post.get("irs") or "").strip()

        # Codes-only requirement: partner must enter a code manually (NOW OPTIONAL)
        partner_code = (post.get("partner_code") or "").strip()

        if partner_role not in ROLE_MAP:
            return request.render(
                "partner_attribution_v1.website_partner_apply",
                {"roles": ROLE_MAP, "selected_role": None, "error": _("Please select a valid Partner Role."), "post": post},
            )

        if not applicant_name:
            return request.render(
                "partner_attribution_v1.website_partner_apply",
                {"roles": ROLE_MAP, "selected_role": partner_role, "error": _("Full Name is required."), "post": post},
            )

        attributed_partner_id = False
        if partner_code:
            attributed_partner = Partner.search(
                [("partner_code", "=", partner_code), ("partner_state", "=", "approved")],
                limit=1,
            )
            if not attributed_partner:
                return request.render(
                    "partner_attribution_v1.website_partner_apply",
                    {
                        "roles": ROLE_MAP,
                        "selected_role": partner_role,
                        "error": _("Invalid Partner Code. Please check and try again, or leave it blank."),
                        "post": post,
                    },
                )
            attributed_partner_id = attributed_partner.id

        inquiry = Inquiry.create({
            "company_id": request.env.company.id,
            "applicant_name": applicant_name,
            "applicant_company": applicant_company or False,
            "email": email or False,
            "phone": phone or False,
            "note": note or False,
            "partner_role": partner_role,
            "vat": vat or False,
            "iban": iban or False,
            "coc": coc or False,
            "irs": irs or False,
            # store attribution directly on inquiry
            "attributed_partner_id": attributed_partner_id,
        })

        inquiry.action_submit_from_website()

        # ensure token exists for status link
        try:
            inquiry._pa_v1_ensure_access_token()
        except Exception:
            pass

        return request.render(
            "partner_attribution_v1.website_partner_apply_done",
            {"inquiry": inquiry, "roles": ROLE_MAP, "role": ROLE_MAP[partner_role]},
        )

    # -----------------------------
    # ✅ Adjustment #2: Application Status (public)
    # -----------------------------
    @http.route("/partners/application/status", type="http", auth="public", website=True, sitemap=True)
    def partners_application_status(self, **kwargs):
        # Show lookup form (ref + token)
        return request.render("partner_attribution_v1.website_partner_application_status", {"post": {}, "error": False})

    @http.route("/partners/application/status/check", type="http", auth="public", website=True, methods=["POST"], csrf=True)
    def partners_application_status_check(self, **post):
        Inquiry = request.env["partner.attribution.inquiry"]
        ref = (post.get("ref") or "").strip()
        token = (post.get("token") or "").strip()

        rec = Inquiry.pa_v1_public_lookup(ref, token)
        if not rec:
            return request.render(
                "partner_attribution_v1.website_partner_application_status",
                {"post": post, "error": _("Invalid reference or token. Please check and try again.")},
            )

        payload = rec._pa_v1_status_payload()
        return request.render(
            "partner_attribution_v1.website_partner_application_status_result",
            {"inquiry": rec, "payload": payload},
        )

    @http.route("/partners/application/<string:ref>", type="http", auth="public", website=True, sitemap=False)
    def partners_application_status_direct(self, ref, token=None, **kwargs):
        Inquiry = request.env["partner.attribution.inquiry"]
        rec = Inquiry.pa_v1_public_lookup(ref, token)
        if not rec:
            # fallback to form (do not leak whether ref exists)
            return request.render(
                "partner_attribution_v1.website_partner_application_status",
                {"post": {"ref": ref, "token": token or ""}, "error": _("Invalid reference or token. Please check and try again.")},
            )

        payload = rec._pa_v1_status_payload()
        return request.render(
            "partner_attribution_v1.website_partner_application_status_result",
            {"inquiry": rec, "payload": payload},
        )

    # -----------------------------
    # Footer required pages
    # -----------------------------
    @http.route("/partners/privacy", type="http", auth="public", website=True, sitemap=True)
    def partners_privacy(self, **kwargs):
        return request.render("partner_attribution_v1.website_partner_privacy", {})

    @http.route("/partners/terms", type="http", auth="public", website=True, sitemap=True)
    def partners_terms(self, **kwargs):
        return request.render("partner_attribution_v1.website_partner_terms", {})

    @http.route("/partners/contact", type="http", auth="public", website=True, sitemap=True)
    def partners_contact(self, **kwargs):
        return request.render("partner_attribution_v1.website_partner_contact", {})
