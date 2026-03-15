# -*- coding: utf-8 -*-
import base64
import secrets
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class PartnerContractTemplate(models.Model):
    _name = "partner.contract.template"
    _description = "Partner Contract Template"
    _order = "active desc, version desc, id desc"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)

    partner_role = fields.Selection(
        selection=[
            ("ap", "Affiliate Partner"),
            ("lead", "Lead Partner"),
            ("sales_agent", "Sales Agent"),
            ("sales_partner", "Sales Partner (Buy–Sell)"),
        ],
        required=True,
        index=True,
    )

    version = fields.Char(required=True, help="Contract version identifier, e.g. v1.0, 2026-02-19")
    effective_date = fields.Date()

    attachment_id = fields.Many2one(
        "ir.attachment",
        string="Contract PDF",
        required=True,
        domain=[("mimetype", "=", "application/pdf")],
        help="Upload the official company-provided PDF here.",
    )

    note = fields.Text()

    _sql_constraints = [
        ("role_version_unique", "unique(partner_role, version)", "A contract version must be unique per role."),
    ]

    def _pa_ensure_attachment_access_token(self):
        """Ensure /web/content links work for portal users via access_token."""
        for rec in self.sudo():
            att = rec.attachment_id.sudo()
            if not att:
                continue
            if hasattr(att, "_ensure_access_token"):
                att._ensure_access_token()
            else:
                if "access_token" in att._fields and not att.access_token:
                    att.write({"access_token": secrets.token_urlsafe(24)})

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs._pa_ensure_attachment_access_token()
        return recs

    def write(self, vals):
        res = super().write(vals)
        if "attachment_id" in vals:
            self._pa_ensure_attachment_access_token()
        return res


class PartnerContractAcceptance(models.Model):
    _name = "partner.contract.acceptance"
    _description = "Partner Contract Acceptance"
    _order = "accepted_on desc, id desc"

    partner_id = fields.Many2one("res.partner", required=True, ondelete="cascade", index=True)
    template_id = fields.Many2one("partner.contract.template", required=True, ondelete="restrict", index=True)

    accepted_on = fields.Datetime(required=True, default=lambda self: fields.Datetime.now(), index=True)
    accepted_ip = fields.Char(string="Accepted IP")
    accepted_user_id = fields.Many2one("res.users", string="Accepted By", default=lambda self: self.env.user, required=True)

    accepted_attachment_id = fields.Many2one(
        "ir.attachment",
        string="Accepted PDF Snapshot",
        required=True,
        help="Immutable copy of the PDF that was accepted.",
    )

    version = fields.Char(related="template_id.version", store=True, readonly=True)
    partner_role = fields.Selection(related="template_id.partner_role", store=True, readonly=True)

    _sql_constraints = [
        ("partner_template_unique", "unique(partner_id, template_id)", "This partner already accepted this template."),
    ]


class ResPartner(models.Model):
    _inherit = "res.partner"

    contract_template_id = fields.Many2one("partner.contract.template", string="Assigned Contract Template", copy=False)
    contract_acceptance_id = fields.Many2one("partner.contract.acceptance", string="Contract Acceptance", copy=False, readonly=True)

    contract_status = fields.Selection(
        [
            ("not_assigned", "Not Assigned"),
            ("pending", "Pending Acceptance"),
            ("accepted", "Accepted"),
        ],
        compute="_compute_contract_status",
        store=False,
    )

    contract_accepted_on = fields.Datetime(related="contract_acceptance_id.accepted_on", readonly=True)
    contract_accepted_ip = fields.Char(related="contract_acceptance_id.accepted_ip", readonly=True)
    contract_version = fields.Char(related="contract_acceptance_id.version", readonly=True)

    @api.depends("contract_template_id", "contract_acceptance_id")
    def _compute_contract_status(self):
        for p in self:
            if not p.contract_template_id:
                p.contract_status = "not_assigned"
            elif p.contract_acceptance_id and p.contract_acceptance_id.template_id.id == p.contract_template_id.id:
                p.contract_status = "accepted"
            else:
                p.contract_status = "pending"

    def action_assign_contract_template(self):
        """Assign latest active template matching partner_role."""
        Template = self.env["partner.contract.template"].sudo()
        for p in self:
            if not p.partner_role:
                raise UserError(_("Partner role is required to assign a contract template."))
            tmpl = Template.search(
                [("active", "=", True), ("partner_role", "=", p.partner_role)],
                order="effective_date desc, version desc, id desc",
                limit=1,
            )
            if not tmpl:
                raise UserError(_("No active contract template found for role: %s") % (p.partner_role,))
            p.sudo().write({"contract_template_id": tmpl.id})
        return True

    def action_download_assigned_contract(self):
        self.ensure_one()
        if not self.contract_template_id or not self.contract_template_id.attachment_id:
            raise UserError(_("No contract PDF assigned to this partner."))
        att = self.contract_template_id.attachment_id
        url = "/web/content/%s?download=true%s" % (
            att.id,
            (att.access_token and ("&access_token=%s" % att.access_token)) or "",
        )
        return {"type": "ir.actions.act_url", "url": url, "target": "self"}

    def action_generate_partner_contract(self):
        """Backward compatible: downloads assigned official template."""
        self.ensure_one()
        return self.action_download_assigned_contract()

    def _create_acceptance_snapshot(self, template, accepted_ip=None):
        self.ensure_one()
        if not template or not template.attachment_id:
            raise ValidationError(_("Template PDF missing."))

        src = template.attachment_id.sudo()
        filename = "Accepted_Contract_%s_%s.pdf" % (getattr(self, "partner_code", False) or self.id, template.version)

        snap = self.env["ir.attachment"].sudo().create({
            "name": filename,
            "type": "binary",
            "datas": src.datas,
            "mimetype": "application/pdf",
            "res_model": "res.partner",
            "res_id": self.id,
        })

        acceptance = self.env["partner.contract.acceptance"].sudo().create({
            "partner_id": self.id,
            "template_id": template.id,
            "accepted_on": fields.Datetime.now(),
            "accepted_ip": accepted_ip or False,
            "accepted_user_id": self.env.user.id,
            "accepted_attachment_id": snap.id,
        })

        self.sudo().write({"contract_acceptance_id": acceptance.id})
        return acceptance