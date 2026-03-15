# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
import re
import secrets

# ✅ FIXED: Import the IBAN validator instead of duplicating the code (Audit #10)
from .partner_compliance_rules import _iban_is_valid


class PartnerAttributionInquiry(models.Model):
    _name = "partner.attribution.inquiry"
    _description = "Partner Inquiry"
    _order = "id desc"
    _rec_name = "name"

    name = fields.Char(string="Inquiry Ref", required=True, copy=False, default=lambda self: _("New"))
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)

    applicant_name = fields.Char(string="Full Name", required=True)
    applicant_company = fields.Char(string="Applicant Company")
    email = fields.Char(string="Email")
    phone = fields.Char(string="Phone")
    note = fields.Text(string="Message / Note")

    partner_role = fields.Selection(
        selection=[
            ("ap", "Affiliate Partner"),
            ("lead", "Lead Partner"),
            ("sales_agent", "Sales Agent"),
            ("sales_partner", "Sales Partner (Buy–Sell)"),
        ],
        string="Partner Role",
        required=True,
    )

    vat = fields.Char(string="VAT / Tax ID")
    iban = fields.Char(string="IBAN")
    coc = fields.Char(string="CoC Number")
    irs = fields.Char(string="IRS / Tax Ref")

    attributed_partner_id = fields.Many2one(
        "res.partner",
        string="Attributed Partner",
        copy=False,
        index=True,
        readonly=True,
        help="Partner who referred/attributed this application (codes-only).",
    )

    attachment_ids = fields.Many2many(
        "ir.attachment",
        "partner_inquiry_attachment_rel",
        "inquiry_id",
        "attachment_id",
        string="Documents",
    )

    state = fields.Selection(
        [
            ("inquiry", "Inquiry"),
            ("enlisted", "Enlisted (Screening)"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
        ],
        default="inquiry",
        required=True,
        index=True,
    )

    partner_id = fields.Many2one("res.partner", string="Created Partner", readonly=True, copy=False)
    crm_lead_id = fields.Many2one("crm.lead", string="CRM Lead", readonly=True, copy=False)
    signup_url = fields.Char(string="Signup / Reset URL", readonly=True, copy=False)

    access_token = fields.Char(
        string="Public Access Token",
        copy=False,
        readonly=True,
        index=True,
        help="Token required to check application status from the website.",
    )
    public_status_message = fields.Char(
        string="Public Status Message",
        copy=False,
        help="Optional safe message visible to applicant on status page (avoid internal notes).",
    )

    def _pa_v1_generate_access_token(self):
        return secrets.token_urlsafe(18)

    def _pa_v1_ensure_access_token(self):
        for rec in self:
            if not rec.access_token:
                rec.sudo().write({"access_token": rec._pa_v1_generate_access_token()})

    def _pa_v1_status_payload(self):
        self.ensure_one()
        role_label = dict(self._fields["partner_role"].selection).get(self.partner_role)
        state_label = dict(self._fields["state"].selection).get(self.state)
        return {
            "ref": self.name,
            "state": self.state,
            "state_label": state_label,
            "role_label": role_label,
            "applicant_name": self.applicant_name,
            "applicant_company": self.applicant_company,
            "public_status_message": self.public_status_message or "",
            "signup_url": self.signup_url or "",
        }

    @api.model
    def pa_v1_public_lookup(self, ref, token):
        ref = (ref or "").strip()
        token = (token or "").strip()
        if not ref or not token:
            return False

        rec = self.sudo().search([("name", "=", ref)], limit=1)
        if not rec:
            return False

        rec._pa_v1_ensure_access_token()
        if rec.access_token != token:
            return False
        return rec

    def _validate_admission(self, stage="approve"):
        for rec in self:
            if not rec.applicant_name:
                raise UserError(_("Full Name is required."))

            if rec.partner_role not in dict(self._fields["partner_role"].selection):
                raise UserError(_("Invalid Partner Role."))

            if stage != "approve":
                return True

            if not rec.email:
                raise UserError(_("Email is required for partner approval."))

            if rec.partner_role in ("sales_agent", "sales_partner"):
                if not rec.iban:
                    raise UserError(_("IBAN is required for this partner role."))
                if not _iban_is_valid(rec.iban):
                    raise UserError(_("Invalid IBAN. Please check formatting and checksum."))

            if rec.partner_role in ("lead", "sales_agent", "sales_partner"):
                if not rec.vat and not rec.coc:
                    raise UserError(_("For this role, please provide at least one company identifier (VAT or CoC)."))

            if rec.partner_role in ("sales_agent", "sales_partner"):
                if not rec.attachment_ids:
                    raise UserError(_("Supporting documents must be uploaded before approval."))

        return True

    def _ensure_crm_lead(self):
        self.ensure_one()
        if "crm.lead" not in self.env:
            return False
        if self.crm_lead_id:
            return self.crm_lead_id

        role_label = dict(self._fields["partner_role"].selection).get(self.partner_role)
        Lead = self.env["crm.lead"].sudo()
        stage_new = self.env.ref("partner_attribution_v1.crm_stage_partner_app_new", raise_if_not_found=False)

        lead_vals = {
            "name": "%s (%s)" % (self.applicant_company or self.applicant_name, role_label),
            "contact_name": self.applicant_name,
            "partner_name": self.applicant_company or False,
            "email_from": self.email or False,
            "phone": self.phone or False,
            "description": "\n".join(filter(None, [
                "Partner Role: %s" % role_label,
                "VAT: %s" % (self.vat or ""),
                "CoC: %s" % (self.coc or ""),
                "IRS: %s" % (self.irs or ""),
                "IBAN: %s" % (self.iban or ""),
                "",
                self.note or "",
            ])).strip() or False,
            "company_id": self.company_id.id,
        }
        if stage_new:
            lead_vals["stage_id"] = stage_new.id

        lead = Lead.create(lead_vals)
        self.sudo().write({"crm_lead_id": lead.id})
        return lead

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name") in (False, _("New"), "New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("partner.attribution.inquiry") or _("New")
        recs = super().create(vals_list)
        recs._pa_v1_ensure_access_token()
        return recs

    def action_submit_from_website(self):
        for rec in self:
            if rec.state in ("approved", "rejected"):
                continue

            rec._pa_v1_ensure_access_token()
            rec._validate_admission(stage="enlist")
            rec._ensure_crm_lead()

            stage_screening = self.env.ref(
                "partner_attribution_v1.crm_stage_partner_app_screening",
                raise_if_not_found=False
            )
            if stage_screening and rec.crm_lead_id:
                rec.crm_lead_id.sudo().write({"stage_id": stage_screening.id})

            if rec.state == "inquiry":
                rec.sudo().write({"state": "enlisted"})
        return True

    def action_enlist_partner(self):
        for rec in self:
            if rec.state != "inquiry":
                continue
            rec._validate_admission(stage="enlist")
            rec._ensure_crm_lead()

            stage_screening = self.env.ref(
                "partner_attribution_v1.crm_stage_partner_app_screening",
                raise_if_not_found=False
            )
            if stage_screening and rec.crm_lead_id:
                rec.crm_lead_id.sudo().write({"stage_id": stage_screening.id})

            rec._pa_v1_ensure_access_token()
            rec.sudo().write({"state": "enlisted"})
        return True

    def action_open_lead(self):
        self.ensure_one()
        if not self.crm_lead_id:
            return True
        return {
            "type": "ir.actions.act_window",
            "name": _("CRM Lead"),
            "res_model": "crm.lead",
            "res_id": self.crm_lead_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_open_partner(self):
        self.ensure_one()
        if not self.partner_id:
            return True
        return {
            "type": "ir.actions.act_window",
            "name": _("Partner"),
            "res_model": "res.partner",
            "res_id": self.partner_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_reject(self):
        for rec in self:
            if rec.state in ("approved", "rejected"):
                continue

            rec._pa_v1_ensure_access_token()

            stage_rejected = self.env.ref(
                "partner_attribution_v1.crm_stage_partner_app_rejected",
                raise_if_not_found=False
            )
            if stage_rejected and rec.crm_lead_id:
                rec.crm_lead_id.sudo().write({"stage_id": stage_rejected.id})

            rec.sudo().write({
                "state": "rejected",
                "public_status_message": rec.public_status_message or _("Your application was rejected."),
            })
        return True

    def _pa_v1_assign_contract_template_if_possible(self, partner):
        """Assign latest active template based on partner_role (if contract module present)."""
        partner = partner.sudo()
        if "contract_template_id" not in partner._fields:
            return False
        if hasattr(partner, "action_assign_contract_template"):
            try:
                partner.action_assign_contract_template()
                return True
            except Exception:
                return False

        # fallback (if method not present)
        Template = self.env["partner.contract.template"].sudo() if "partner.contract.template" in self.env else False
        if not Template:
            return False
        if not partner.partner_role:
            return False

        tmpl = Template.search(
            [("active", "=", True), ("partner_role", "=", partner.partner_role)],
            order="effective_date desc, version desc, id desc",
            limit=1,
        )
        if tmpl:
            partner.write({"contract_template_id": tmpl.id})
            return True
        return False

    def action_approve_partner(self):
        Partner = self.env["res.partner"].sudo()

        for rec in self:
            if rec.state != "enlisted":
                raise UserError(_("Only Enlisted inquiries can be Approved."))

            # strict checks here (Requirement #3)
            self.env["partner.compliance.rules"].validate_inquiry_for_approval(rec)

            partner = False
            if rec.email:
                partner = Partner.search([("email", "=", (rec.email or "").strip())], limit=1)
            if not partner and rec.phone:
                partner = Partner.search([("phone", "=", (rec.phone or "").strip())], limit=1)

            vals = {
                "name": rec.applicant_name,
                "email": (rec.email or "").strip() or False,
                "phone": (rec.phone or "").strip() or False,
                "company_type": "company" if rec.applicant_company else "person",
                "comment": "\n".join(filter(None, [
                    ("Company: %s" % rec.applicant_company) if rec.applicant_company else "",
                    rec.note or "",
                ])).strip() or False,
            }

            # ✅ Write VAT/CoC/IRS to partner if fields exist
            if "vat" in Partner._fields and rec.vat:
                vals["vat"] = rec.vat
            if "coc_number" in Partner._fields and rec.coc:
                vals["coc_number"] = rec.coc
            if "irs_tax_ref" in Partner._fields and rec.irs:
                vals["irs_tax_ref"] = rec.irs

            if partner:
                partner.write(vals)
            else:
                partner = Partner.create(vals)

            # Bank
            if rec.iban:
                Bank = self.env["res.partner.bank"].sudo()
                iban_clean = (rec.iban or "").replace(" ", "").upper()
                existing = Bank.search([("partner_id", "=", partner.id), ("acc_number", "=", iban_clean)], limit=1)
                if not existing:
                    Bank.create({"partner_id": partner.id, "acc_number": iban_clean})

            # Role
            if "partner_role" in partner._fields:
                partner.write({"partner_role": rec.partner_role})

            # Approve + provision portal (your res_partner.py already handles best-effort portal token/user)
            if hasattr(partner, "action_approve_partner"):
                partner.action_approve_partner()
            else:
                if "partner_state" in partner._fields:
                    partner.write({"partner_state": "approved"})

            # ✅ Auto-assign contract template (so portal/contracts is functional)
            rec._pa_v1_assign_contract_template_if_possible(partner)

            # Attach inquiry docs onto partner
            if rec.attachment_ids:
                rec.attachment_ids.sudo().write({"res_model": "res.partner", "res_id": partner.id})

            # Store signup url
            signup_url = False
            if "signup_url" in partner._fields:
                signup_url = partner.signup_url
            if not signup_url and "portal_invite_url" in partner._fields:
                signup_url = partner.portal_invite_url

            stage_approved = self.env.ref(
                "partner_attribution_v1.crm_stage_partner_app_approved",
                raise_if_not_found=False
            )
            if stage_approved and rec.crm_lead_id:
                rec.crm_lead_id.sudo().write({"stage_id": stage_approved.id})

            rec._pa_v1_ensure_access_token()
            rec.sudo().write({
                "partner_id": partner.id,
                "signup_url": signup_url or False,
                "state": "approved",
                "public_status_message": rec.public_status_message or _("Approved. You can login using the invite link."),
            })

        return True
