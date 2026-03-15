# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class PartnerAttributionLedger(models.Model):
    _name = "partner.attribution.ledger"
    _description = "Partner Attribution Ledger"
    _order = "id desc"
    _rec_name = "display_name"

    display_name = fields.Char(string="Reference", compute="_compute_display_name", store=True)

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one("res.currency", related="company_id.currency_id", store=True, readonly=True)

    partner_id = fields.Many2one("res.partner", string="Attributed Partner", required=True, index=True)

    invoice_id = fields.Many2one(
        "account.move",
        string="Customer Invoice/Refund",
        required=True,
        index=True,
        ondelete="restrict",
    )
    origin_invoice_id = fields.Many2one(
        "account.move",
        string="Origin Invoice (if refund)",
        index=True,
        ondelete="restrict",
    )

    entry_type = fields.Selection(
        [("invoice", "Invoice"), ("refund", "Refund"), ("lead_reward", "Lead Reward")],
        string="Type",
        required=True,
        default="invoice",
        index=True,
    )

    commission_rate_used = fields.Float(string="Commission Rate Used (%)", readonly=True)
    commission_amount = fields.Monetary(string="Amount (Signed)", readonly=True)

    state = fields.Selection(
        [("on_hold", "On Hold"), ("payable", "Payable"), ("paid", "Paid"), ("reversed", "Reversed")],
        string="Status",
        required=True,
        default="on_hold",
        index=True,
    )

    hold_reason = fields.Char(
        string="Hold Reason",
        readonly=True,
        help="If status is On Hold, explains why (KYC, bank, blocked, etc.).",
    )

    invoice_paid_at = fields.Datetime(string="Invoice Paid At", readonly=True)
    created_at = fields.Datetime(string="Created At", default=fields.Datetime.now, readonly=True)

    vendor_bill_id = fields.Many2one(
        "account.move",
        string="Vendor Bill",
        index=True,
        ondelete="restrict",
        readonly=True,
        copy=False,
    )
    vendor_bill_payment_state = fields.Selection(related="vendor_bill_id.payment_state", store=False, readonly=True)

    payout_batch_id = fields.Many2one(
        "partner.attribution.payout.batch",
        string="Payout Batch",
        index=True,
        ondelete="set null",
        readonly=True,
        copy=False,
    )

    partner_kyc_status = fields.Selection(related="partner_id.kyc_status", store=True, readonly=True)

    _sql_constraints = [
        # ✅ allow multiple lines per invoice (invoice + lead_reward)
        ("uniq_invoice_type_ledger", "unique(invoice_id, entry_type)", "A ledger line already exists for this invoice/type."),
    ]

    @api.depends("invoice_id", "partner_id", "entry_type")
    def _compute_display_name(self):
        for rec in self:
            inv = rec.invoice_id
            rec.display_name = "%s | %s | %s" % (
                inv.name or inv.ref or _("Invoice"),
                rec.partner_id.display_name or _("Partner"),
                rec.entry_type,
            )

    def unlink(self):
        if self.env.context.get("module_uninstall") or self.env.context.get("force_unlink_ledger"):
            return super().unlink()
        if not self:
            return True

        ctx = self.env.context
        in_test = bool(getattr(self.env.registry, "in_test_mode", lambda: False)())
        if ctx.get("install_mode") or ctx.get("module_uninstall") or in_test:
            return super().unlink()

        raise UserError(_("Ledger lines are audit records and cannot be deleted."))

    def write(self, vals):
        immutable = {
            "company_id", "partner_id", "invoice_id", "origin_invoice_id",
            "entry_type", "commission_rate_used", "commission_amount",
            "invoice_paid_at", "created_at",
        }
        if immutable.intersection(vals.keys()):
            raise UserError(_("Ledger lines are audit records. Core fields cannot be edited."))
        return super().write(vals)

    def action_recompute_payout_state(self):
        """
        Rules:
        - refund => reversed
        - lead_reward => payable logic based on partner KYC/bank
        - invoice => payable logic based on role eligibility + partner KYC/bank
        - vendor bill paid => paid
        """
        for line in self.sudo():
            if line.entry_type == "refund":
                line.state = "reversed"
                line.hold_reason = _("Refund / reversal")
                continue

            partner = line.partner_id

            if line.vendor_bill_id and line.vendor_bill_id.payment_state in ("paid", "in_payment"):
                line.state = "paid"
                line.hold_reason = False
                continue

            # The ledger now acts as the gatekeeper checking the invoice payment!
            if line.invoice_id and line.invoice_id.payment_state not in ("paid", "in_payment"):
                line.state = "on_hold"
                line.hold_reason = _("Awaiting Customer Payment")
                continue

            if not line.commission_amount or line.commission_amount <= 0:
                line.state = "on_hold"
                line.hold_reason = _("No amount")
                continue

            if line.entry_type == "invoice":
                role = getattr(partner, "partner_role", False)
                # FIX: Sales Agents and APs are eligible. Leads only get 'lead_reward' entry_type.
                if role not in ("ap", "sales_agent"):
                    line.state = "on_hold"
                    line.hold_reason = _("Role not commission-eligible")
                    continue

            if getattr(partner, "kyc_blocked", False):
                line.state = "on_hold"
                line.hold_reason = _("KYC blocked")
                continue

            kyc_status = getattr(partner, "kyc_status", "not_submitted")
            if kyc_status not in ("complete", "verified"):
                line.state = "on_hold"
                line.hold_reason = _("KYC not complete")
                continue

            if not getattr(partner, "bank_verified", False):
                line.state = "on_hold"
                line.hold_reason = _("Bank not verified")
                continue

            # If it passes all checks, it is payable!
            line.state = "payable"
            line.hold_reason = False

        return True
