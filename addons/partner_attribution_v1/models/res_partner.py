# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError, UserError
import base64
import re
import logging


from .partner_compliance_rules import _iban_is_valid

_logger = logging.getLogger(__name__)

try:
    import psycopg2
except Exception:
    psycopg2 = None
    

class ResPartner(models.Model):
    _inherit = "res.partner"

    # ----------------------------
    # KYC fields
    # ----------------------------
    kyc_status = fields.Selection(
        selection=[
            ("not_submitted", "Not Submitted"),
            ("pending", "Pending Review"),
            ("verified", "Verified"),
            ("complete", "Complete"),
            ("rejected", "Rejected"),
        ],
        string="KYC Status",
        default="not_submitted",
        copy=False,
        tracking=True,  
        store=True,
        readonly=False,
    )
    kyc_note = fields.Text(string="KYC Notes", copy=False)
    kyc_verified_on = fields.Datetime(string="KYC Verified On", copy=False, readonly=True)

    kyc_blocked = fields.Boolean(
        string="KYC Blocked",
        default=False,
        copy=False,
        tracking=True,
        store=True,
        readonly=False,
        help="If enabled, this partner should be treated as blocked for payouts/approval.",
    )

    bank_verified = fields.Boolean(default=False, copy=False, tracking=True, store=True, readonly=False)
    bank_verified_on = fields.Datetime(string="Bank Verified On", copy=False, readonly=True)

    company_verified = fields.Boolean(default=False, copy=False, tracking=True, store=True, readonly=False)
    company_verified_on = fields.Datetime(string="Company Verified On", copy=False, readonly=True)

    vat_verified = fields.Boolean(default=False, copy=False, tracking=True, store=True, readonly=False)
    vat_verified_on = fields.Datetime(string="VAT Verified On", copy=False, readonly=True)

    coc_number = fields.Char(string="CoC Number", copy=False, tracking=False)
    irs_tax_ref = fields.Char(string="IRS / Tax Ref", copy=False, tracking=False)

    # ----------------------------
    # Partner Attribution fields
    # ----------------------------
    partner_role = fields.Selection(
        selection=[
            ("ap", "Affiliate Partner"),
            ("lead", "Lead Partner"),
            ("sales_agent", "Sales Agent"),
            ("sales_partner", "Sales Partner (Buy–Sell)"),
        ],
        string="Partner Role",
        copy=False,
        tracking=True,
    )

    partner_state = fields.Selection(
        selection=[("draft", "Draft"), ("approved", "Approved")],
        string="Partner Status",
        default="draft",
        copy=False,
        tracking=True,
        store=True,
        readonly=True,
    )

    partner_code = fields.Char(string="Partner Code", copy=False, readonly=True, index=True, tracking=False)
    partner_uid = fields.Char(string="Partner ID", copy=False, readonly=True, index=True, tracking=False)

    compliance_missing = fields.Char(
        string="Compliance Missing",
        compute="_compute_compliance_missing",
        store=False,
        readonly=True,
        help="Shows missing items required for approval based on role.",
    )

    _sql_constraints = [
        ("partner_code_unique", "unique(partner_code)", "Partner Code must be unique."),
        ("partner_uid_unique", "unique(partner_uid)", "Partner ID must be unique."),
    ]

    # ----------------------------
    # Commission (REAL DB COLUMN)
    # ----------------------------
    commission_rate = fields.Float(
        string="Commission Rate (%)",
        default=5.0,
        copy=False,
        help="Commission percentage used later to generate vendor bills from posted customer invoices.",
    )

    lead_reward_amount = fields.Monetary(
        string="Lead Reward Amount",
        currency_field="currency_id",
        help="Fixed reward paid when a Lead Partner lead converts and the resulting invoice is PAID.",
        tracking=True,
    )

    @api.constrains("commission_rate")
    def _check_commission_rate(self):
        for rec in self:
            rate = rec.commission_rate or 0.0
            if rate < 0 or rate > 100:
                raise ValidationError(_("Commission Rate must be between 0 and 100."))

    # ----------------------------
    # Portal URLs (computed only)
    # ----------------------------
    portal_invite_url = fields.Char(compute="_compute_portal_invite_url", store=False, readonly=True)
    signup_url = fields.Char(compute="_compute_signup_url", store=False, readonly=True)

    def _compute_signup_url(self):
        base_url = (self.env["ir.config_parameter"].sudo().get_param("web.base.url") or "").rstrip("/")
        has_signup_token = "signup_token" in self._fields
        for rec in self:
            token = getattr(rec, "signup_token", False) if has_signup_token else False
            rec.signup_url = f"{base_url}/web/signup?token={token}" if (base_url and token) else False

    def _compute_portal_invite_url(self):
        for rec in self:
            rec.portal_invite_url = rec.signup_url or False

    # ----------------------------
    # SAFE portal provisioning
    # ----------------------------
    def _pa_can_provision_portal(self) -> bool:
        if "signup_token" not in self._fields or not hasattr(self, "signup_prepare"):
            return False
        try:
            self.env.ref("base.group_portal")
        except Exception:
            return False
        return True

    def _pa_ensure_portal_user_and_signup_token_safe(self):
        self.ensure_one()
        email = (self.email or "").strip().lower()
        if not email or not self._pa_can_provision_portal():
            return False

        Users = self.env["res.users"].sudo()
        portal_group = self.env.ref("base.group_portal")
        internal_group = self.env.ref("base.group_user")

        user = Users.search([("login", "=", email)], limit=1)
        if not user:
            user = Users.create({
                "name": self.name or email,
                "login": email,
                "email": email,
                "partner_id": self.id,
                "groups_id": [(6, 0, [portal_group.id])],
                "active": True,
            })
        else:
            if internal_group.id in user.groups_id.ids:
                user.write({"groups_id": [(3, internal_group.id), (4, portal_group.id)]})
            elif portal_group.id not in user.groups_id.ids:
                user.write({"groups_id": [(4, portal_group.id)]})

        try:
            self.sudo().signup_prepare()
        except Exception as e:
            _logger.error("Signup prepare failed for partner %s: %s", self.id, str(e))
            return False

        base_url = (self.env["ir.config_parameter"].sudo().get_param("web.base.url") or "").rstrip("/")
        token = getattr(self, "signup_token", False)
        return f"{base_url}/web/signup?token={token}" if (base_url and token) else False

    def action_generate_portal_invite_link(self):
        self.ensure_one()
        if not (self.email or "").strip():
            raise UserError(_("This partner needs an Email to receive an invite."))

        url = self._pa_ensure_portal_user_and_signup_token_safe()
        if not url:
            raise UserError(_("Signup URL was not generated. Check auth_signup configuration."))

        user = self.env['res.users'].sudo().search([('partner_id', '=', self.id)], limit=1)
        if user:
            user.with_context(signup_force_type_invite=True).action_reset_password()

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Invite Sent"),
                "message": _("An invitation email has been sent to %s") % self.email,
                "sticky": False,
                "type": "success"
            },
        }

    # ----------------------------
    # Contract PDF download
    # ----------------------------
    def action_generate_partner_contract(self):
        self.ensure_one()

        if "contract_template_id" in self._fields and getattr(self, "contract_template_id", False):
            tmpl = self.contract_template_id
            if tmpl and tmpl.attachment_id:
                return {
                    "type": "ir.actions.act_url",
                    "url": f"/web/content/{tmpl.attachment_id.id}?download=true",
                    "target": "self",
                }

        report_action = self.env.ref("partner_attribution_v1.action_report_partner_contract", raise_if_not_found=False)
        if not report_action:
            raise UserError(_("Partner Contract report is not configured."))

        report_xmlid = "partner_attribution_v1.action_report_partner_contract"
        pdf, _ = self.env["ir.actions.report"].sudo()._render_qweb_pdf(report_xmlid, self.ids)
        if not pdf:
            raise UserError(_("Contract PDF could not be generated (empty output)."))

        filename = f"Partner_Contract_{self.partner_code or self.id}.pdf"
        attachment = self.env["ir.attachment"].sudo().create({
            "name": filename,
            "type": "binary",
            "datas": base64.b64encode(pdf),
            "mimetype": "application/pdf",
            "res_model": "res.partner",
            "res_id": self.id,
        })
        return {"type": "ir.actions.act_url", "url": f"/web/content/{attachment.id}?download=true", "target": "self"}

    # ----------------------------
    # KYC Actions
    # ----------------------------
    def action_set_kyc_pending(self):
        self.write({"kyc_status": "pending"})

    def action_set_kyc_verified(self):
        self.write({"kyc_status": "verified", "kyc_verified_on": fields.Datetime.now(), "kyc_blocked": False})

    def action_set_kyc_complete(self):
        for partner in self:
            partner.write({
                "kyc_status": "complete",
                "kyc_verified_on": partner.kyc_verified_on or fields.Datetime.now(),
                "kyc_blocked": False,
            })

    def action_set_kyc_rejected(self):
        self.write({"kyc_status": "rejected", "kyc_verified_on": False})

    def action_set_kyc_blocked(self):
        self.write({"kyc_blocked": True})

    def action_set_kyc_unblocked(self):
        self.write({"kyc_blocked": False})

    def action_set_bank_verified(self):
        self.write({"bank_verified": True, "bank_verified_on": fields.Datetime.now()})

    def action_set_bank_unverified(self):
        self.write({"bank_verified": False, "bank_verified_on": False})

    def action_set_company_verified(self):
        self.write({"company_verified": True, "company_verified_on": fields.Datetime.now()})

    def action_set_company_unverified(self):
        self.write({"company_verified": False, "company_verified_on": False})

    def action_set_vat_verified(self):
        self.write({"vat_verified": True, "vat_verified_on": fields.Datetime.now()})

    def action_set_vat_unverified(self):
        self.write({"vat_verified": False, "vat_verified_on": False})

    # ----------------------------
    # Compliance helpers
    # ----------------------------
    def _pa_has_documents(self) -> bool:
        self.ensure_one()
        commercial = self.commercial_partner_id or self
        return self.env["ir.attachment"].sudo().search_count([
            ("res_model", "=", "res.partner"),
            ("res_id", "=", commercial.id),
        ]) > 0

    def _pa_get_primary_iban(self) -> str:
        self.ensure_one()
        bank = self.env["res.partner.bank"].sudo().search([("partner_id", "=", self.id)], limit=1)
        return (bank.acc_number or "").replace(" ", "").upper() if bank else ""

    def _pa_validate_for_approval(self):
        for partner in self.sudo():
            if not partner.partner_role:
                raise ValidationError(_("Please set Partner Role before approval."))
            if partner.kyc_blocked:
                raise ValidationError(_("Partner is KYC blocked and cannot be approved."))
            if not (partner.email or "").strip():
                raise ValidationError(_("Email is required for partner approval (portal access)."))

            if partner.partner_role in ("lead", "sales_agent", "sales_partner"):
                vat_val = (getattr(partner, 'vat', '') or "").strip()
                coc_val = (partner.coc_number or "").strip()
                if not (vat_val or coc_val):
                    raise ValidationError(_("For this role, please provide a VAT/Tax ID or CoC Number."))

            if partner.partner_role in ("sales_agent", "sales_partner"):
                iban = partner._pa_get_primary_iban()
                if not iban:
                    raise ValidationError(_("A bank account (IBAN) is required for this role."))
                if not _iban_is_valid(iban):
                    raise ValidationError(_("Invalid IBAN (checksum failed): %s") % iban)
                if not partner._pa_has_documents():
                    raise ValidationError(_("Supporting documents are required before approval."))
        return True

    @api.depends("partner_role", "email", "vat", "coc_number", "irs_tax_ref", "kyc_blocked")
    def _compute_compliance_missing(self):
        for partner in self:
            missing = []
            if not partner.partner_role:
                missing.append("Role")
            if not (partner.email or "").strip():
                missing.append("Email")
            if partner.kyc_blocked:
                missing.append("KYC Blocked")

            if partner.partner_role in ("lead", "sales_agent", "sales_partner"):
                vat_val = (partner.vat or "").strip() if "vat" in partner._fields else ""
                coc_val = (partner.coc_number or "").strip()
                if not (vat_val or coc_val):
                    missing.append("VAT/CoC")

            if partner.partner_role in ("sales_agent", "sales_partner"):
                iban = partner._pa_get_primary_iban()
                if not iban:
                    missing.append("IBAN")
                elif not _iban_is_valid(iban):
                    missing.append("IBAN invalid")
                if not partner._pa_has_documents():
                    missing.append("Documents")

            partner.compliance_missing = ", ".join(missing) if missing else _("OK")

    # ----------------------------
    # Sequences + approval workflow
    # ----------------------------
    def _pick_and_cleanup_sequence(self, seq_code: str):
        Sequence = self.env["ir.sequence"].sudo()
        seqs = Sequence.search([
            ("code", "=", seq_code),
            ("active", "=", True),
            ("company_id", "in", [False, self.env.company.id]),
        ])
        if not seqs:
            return False
        if len(seqs) > 1:
            def score(s):
                next_num = getattr(s, "number_next_actual", s.number_next)
                return (bool(s.company_id and s.company_id.id == self.env.company.id), int(next_num or 0), int(s.id or 0))
            best = max(seqs, key=score)
            (seqs - best).write({"active": False})
            return best
        return seqs[0]

    def _next_sequence_or_raise(self, seq_code: str, label: str) -> str:
        seq = self._pick_and_cleanup_sequence(seq_code)
        if not seq:
            raise UserError(_("%s sequence not found/misconfigured.") % label)
        value = seq.next_by_id()
        if not value:
            raise UserError(_("%s sequence could not generate a number.") % label)
        return value

    def _is_unique_violation(self, err: Exception) -> bool:
        msg = (str(err) or "").lower()
        # Safer check: verify psycopg2 and its Error class exist before using isinstance
        if psycopg2 and hasattr(psycopg2, 'Error') and isinstance(err, psycopg2.Error):
            if getattr(err, "pgcode", None) == "23505":
                return True
        return (
            "duplicate key value violates unique constraint" in msg
            or "partner_code_unique" in msg
            or "partner_uid_unique" in msg
        )

    def _ensure_partner_codes(self):
        UID_SEQ = "partner_attribution.partner_uid"
        CODE_SEQ = "partner_attribution.partner_code"

        for partner in self.sudo():
            if partner.partner_state != "approved":
                continue
            if not partner.partner_role:
                raise ValidationError(_("Please set Partner Role before approval."))
            if partner.partner_uid and partner.partner_code:
                continue

            last_err = None
            for _attempt in range(10):
                vals = {}
                if not partner.partner_uid:
                    vals["partner_uid"] = partner._next_sequence_or_raise(UID_SEQ, "Partner ID")
                if not partner.partner_code:
                    vals["partner_code"] = partner._next_sequence_or_raise(CODE_SEQ, "Partner Code")
                try:
                    with self.env.cr.savepoint():
                        partner.write(vals)
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    if self._is_unique_violation(e):
                        continue
                    raise

            if last_err:
                raise UserError(_("Could not generate a unique Partner ID/Code after multiple attempts."))

    def action_approve_partner(self):
        for partner in self.sudo():
            if partner.partner_state != "approved":
                partner._pa_validate_for_approval()
                partner.write({"partner_state": "approved"})

            partner._ensure_partner_codes()

            if "contract_template_id" in partner._fields and not partner.contract_template_id:
                if hasattr(partner, "action_assign_contract_template"):
                    try:
                        partner.action_assign_contract_template()
                    except Exception:
                        pass

            partner._pa_ensure_portal_user_and_signup_token_safe()

    def action_reset_to_draft(self):
        self.sudo().write({"partner_state": "draft"})


    @api.model_create_multi
    def create(self, vals_list):
        # 1. Standard Odoo creation
        partners = super(ResPartner, self).create(vals_list)
        
        # 2. Post-creation logic for approved partners
        for partner in partners:
            if partner.partner_state == "approved":
                # Ensure they meet compliance and have ID/Codes generated
                partner._pa_validate_for_approval()
                partner._ensure_partner_codes()
                partner._pa_ensure_portal_user_and_signup_token_safe()
        
        return partners
        

    def write(self, vals):
        if "partner_uid" in vals:
            for p in self:
                if p.partner_uid and vals["partner_uid"] != p.partner_uid:
                    raise ValidationError(_("Partner ID is immutable once generated."))

        if "partner_code" in vals:
            for p in self:
                if p.partner_code and vals["partner_code"] != p.partner_code:
                    raise ValidationError(_("Partner Code is immutable once generated."))

        # FIX: Only run validation if the state is actively TRANSITIONING from draft to approved.
        if vals.get("partner_state") == "approved":
            for partner in self:
                if partner.partner_state != "approved":
                    partner._pa_validate_for_approval()

        res = super().write(vals)

        if vals.get("partner_state") == "approved":
            self._ensure_partner_codes()

        return res
