# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class AccountMove(models.Model):
    _inherit = "account.move"

    # ----------------------------
    # Attribution fields
    # ----------------------------
    attributed_partner_id = fields.Many2one(
        "res.partner",
        string="Attributed Partner",
        readonly=False,
        copy=False,
        index=True,
        help="Attribution copied from Sales Order (locked on post) or set manually (no cookies/session).",
        domain=[("partner_state", "=", "approved")],
    )
    attribution_locked = fields.Boolean(string="Attribution Locked", default=False, copy=False)
    attribution_locked_at = fields.Datetime(string="Attribution Locked At", readonly=True, copy=False)
    attribution_locked_by = fields.Many2one("res.users", string="Attribution Locked By", readonly=True, copy=False)


    partner_payout_batch_id = fields.Many2one(
        "partner.attribution.payout.batch",
        string="Partner Payout Batch",
        copy=False,
        index=True,
        ondelete="set null",
    )

    # ----------------------------
    # Commission / Vendor bill
    # ----------------------------
    commission_vendor_bill_id = fields.Many2one(
        "account.move",
        string="Commission Vendor Bill",
        readonly=True,
        copy=False,
        help="Vendor Bill created for this invoice commission.",
    )

    commission_rate_used = fields.Float(
        string="Commission Rate Used (%)",
        compute="_compute_commission_values",
        store=True,
        readonly=True,
        help="Snapshot of commission rate used for commission calculation.",
    )

    commission_amount = fields.Monetary(
        string="Commission Amount",
        currency_field="currency_id",
        compute="_compute_commission_values",
        store=True,
        readonly=True,
    )

    commission_bill_state = fields.Selection(
        [("none", "No Commission"), ("pending", "Pending"), ("billed", "Billed")],
        string="Commission Status",
        compute="_compute_commission_bill_state",
        store=True,
        readonly=True,
    )

    def _find_partner_by_code(self, code):
        code = (code or "").strip()
        if not code:
            return self.env["res.partner"]
        return self.env["res.partner"].sudo().search(
            [("partner_code", "=", code), ("partner_state", "=", "approved")],
            limit=1,
        )

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        return moves

    # ----------------------------
    # Commission compute
    # ----------------------------
    @api.depends("attributed_partner_id", "amount_untaxed", "currency_id", "move_type")
    def _compute_commission_values(self):
        for move in self:
            rate = 0.0
            if move.attributed_partner_id:
                rate = float(move.attributed_partner_id.commission_rate or 0.0)

            move.commission_rate_used = rate

            amt = (move.amount_untaxed or 0.0) * (rate / 100.0) if rate else 0.0
            if move.move_type == "out_refund" and amt:
                amt = -abs(amt)

            move.commission_amount = amt

    @api.depends("commission_vendor_bill_id", "commission_amount")
    def _compute_commission_bill_state(self):
        for move in self:
            if not move.commission_amount:
                move.commission_bill_state = "none"
            elif move.commission_vendor_bill_id:
                move.commission_bill_state = "billed"
            else:
                move.commission_bill_state = "pending"

    def _pa_v1_partner_role_is_commission_eligible(self, partner):
        partner = (partner or self.env["res.partner"]).sudo()
        role = getattr(partner, "partner_role", False)
        return role in ("ap", "sales_agent")

    def _pa_v1_get_commission_expense_account(self):
        self.ensure_one()
        company = self.company_id or self.env.company
        
        # Use target company context strictly to bypass cross-company security blocks
        Account = self.env["account.account"].sudo().with_company(company)

        # Build domain based on Odoo 17 or Odoo 18 architecture
        domain = [("deprecated", "=", False), ("account_type", "in", ("expense", "expense_direct_cost"))]
        if "company_ids" in Account._fields:
            domain.append(("company_ids", "in", company.ids))
        else:
            domain.append(("company_id", "=", company.id))
            
        acc = Account.search(domain, limit=1)

        if not acc:
            try:
                # Find an unused code starting from 610000
                code = "610000"
                while Account.search([("code", "=", code)]):
                    code = str(int(code) + 1)
                    
                vals = {"name": "Partner Commissions", "code": code, "account_type": "expense"}
                if "company_ids" in Account._fields:
                    vals["company_ids"] = [(6, 0, company.ids)]
                else:
                    vals["company_id"] = company.id
                
                acc = Account.create(vals)

                # CRITICAL: Force Odoo to save the new account to the database and clear its 
                # short-term memory so the Vendor Bill generation can "see" it immediately.
                # This prevents the "Unbalanced Journal Entry" error.
                self.env.flush_all()
                self.env.invalidate_all()
                
            except Exception as e:
                raise UserError(_(
                    "Auto-creation of the Expense account failed!\n\n"
                    "Odoo System Error: %s\n\n"
                    "MANUAL FIX:\n"
                    "1. Switch your active company to '%s' in the top right corner of Odoo.\n"
                    "2. Go to Accounting -> Configuration -> Chart of Accounts\n"
                    "3. Create at least one account with Type = Expense (or Direct Costs)."
                ) % (str(e), company.name))

        return acc
        

    def _pa_v1_partner_is_commission_eligible(self, partner):
        partner = partner.sudo()

        if not self._pa_v1_partner_role_is_commission_eligible(partner):
            return False, _("Role not commission-eligible")

        if getattr(partner, "kyc_blocked", False):
            return False, _("KYC blocked")
        kyc_status = getattr(partner, "kyc_status", "not_submitted")
        if kyc_status not in ("complete", "verified"):
            return False, _("KYC not complete")
        if not getattr(partner, "bank_verified", False):
            return False, _("Bank not verified")
        return True, False

    def _pa_v1_should_create_commission_bill(self):
        self.ensure_one()
        if not self.attributed_partner_id:
            return False

        if not self._pa_v1_partner_role_is_commission_eligible(self.attributed_partner_id.commercial_partner_id):
            return False

        return bool(
            self.move_type == "out_invoice"
            and self.state == "posted"
            and self.payment_state in ("paid", "in_payment")
            and (self.commission_amount or 0.0) > 0.0
            and not self.commission_vendor_bill_id
        )

    def _pa_v1_create_commission_vendor_bill(self, autopost=True):
        self.ensure_one()
        if not self._pa_v1_should_create_commission_bill():
            return self.commission_vendor_bill_id or False

        company = self.company_id or self.env.company
        partner = self.attributed_partner_id.commercial_partner_id.with_company(company)

        eligible, reason = self._pa_v1_partner_is_commission_eligible(partner)
        if not eligible:
            return False

        expense_acc = self._pa_v1_get_commission_expense_account()
        ref_name = self.name or self.payment_reference or str(self.id)

        payable = partner.property_account_payable_id
        if not payable:
            default_payable = company.account_payable_id
            if not default_payable:
                raise UserError(_(
                    "Cannot create Commission Vendor Bill because no payable account is configured.\n\n"
                    "Fix one of these:\n"
                    "• Set a payable account on the vendor (property_account_payable_id)\n"
                    "• Or set a default payable account on the company (account_payable_id)\n\n"
                    "Company: %s"
                ) % (company.display_name,))
            partner.sudo().with_company(company).property_account_payable_id = default_payable

        journal = self.env["account.journal"].sudo().search([
            ("type", "=", "purchase"),
            ("company_id", "=", company.id),
        ], limit=1)
        if not journal:
            raise UserError(_(
                "Cannot create Commission Vendor Bill because no Purchase Journal exists for company '%s'."
            ) % (company.display_name,))

        bill_currency = self.currency_id or company.currency_id

        bill_vals = {
            "move_type": "in_invoice",
            "partner_id": partner.id,
            "company_id": company.id,
            "journal_id": journal.id,
            "currency_id": bill_currency.id,
            "invoice_date": fields.Date.context_today(self),
            "ref": _("Commission for %s") % ref_name,
            "invoice_origin": self.name or "",
            "invoice_line_ids": [(0, 0, {
                "name": _("Commission for Invoice %s") % ref_name,
                "quantity": 1.0,
                "price_unit": float(self.commission_amount or 0.0),
                "account_id": expense_acc.id,
            })],
        }

        bill = self.env["account.move"].sudo().with_company(company).with_context(allowed_company_ids=company.ids).create(bill_vals)
        if autopost:
            try:
                bill.action_post()
            except Exception as e:
                raise UserError(_(
                    "Commission Vendor Bill was created but could not be posted.\n\n"
                    "Bill: %s\nError: %s"
                ) % (bill.display_name, str(e)))

        self.sudo().write({"commission_vendor_bill_id": bill.id})
        return bill

    def action_create_commission_bill(self):
        for move in self:
            if move.move_type != "out_invoice":
                raise UserError(_("Commission bill can only be created from a Customer Invoice."))
            if move.state != "posted":
                raise UserError(_("Please post the Customer Invoice first."))
            
            # Updated to support Odoo 18's in_payment state
            if move.payment_state not in ("paid", "in_payment"):
                raise UserError(_("Commission bill can only be created after the invoice is PAID."))
            
            if not move.attributed_partner_id:
                raise UserError(_("This invoice has no Attributed Partner."))

            if not move._pa_v1_partner_role_is_commission_eligible(move.attributed_partner_id.commercial_partner_id):
                raise UserError(_("This partner role is not commission-eligible. No commission bill can be created."))

            if (move.commission_amount or 0.0) <= 0.0:
                raise UserError(_("Commission amount is 0. Nothing to bill."))
            if move.commission_vendor_bill_id:
                continue

            eligible, reason = move._pa_v1_partner_is_commission_eligible(
                move.attributed_partner_id.commercial_partner_id
            )
            if not eligible:
                raise UserError(_("Cannot create commission bill: %s") % (reason,))

            move._pa_v1_create_commission_vendor_bill(autopost=True)

        if len(self) == 1 and self.commission_vendor_bill_id:
            return {
                "type": "ir.actions.act_window",
                "name": _("Commission Vendor Bill"),
                "res_model": "account.move",
                "res_id": self.commission_vendor_bill_id.id,
                "view_mode": "form",
                "target": "current",
            }
        return True
    
    # ----------------------------
    # Lock behavior (lock on POST)
    # ----------------------------
    def _lock_attribution(self):
        for move in self:
            if move.attribution_locked:
                continue
            if not move.attributed_partner_id:
                raise ValidationError(_("Cannot lock invoice attribution without an Attributed Partner."))

            locked_by = move.attribution_locked_by.id if move.attribution_locked_by else self.env.user.id

            move.sudo().write({
                "attribution_locked": True,
                "attribution_locked_at": move.attribution_locked_at or fields.Datetime.now(),
                "attribution_locked_by": locked_by,
            })

    # ----------------------------
    # Ledger creation rules (commission ledger)
    # ----------------------------
    def _should_create_partner_ledger(self):
        self.ensure_one()
        if not self.attributed_partner_id:
            return False

        if not self._pa_v1_partner_role_is_commission_eligible(self.attributed_partner_id.commercial_partner_id):
            return False

        # Ledger creates immediately upon posting. No waiting for payment_state!
        return bool(
            self.move_type in ("out_invoice", "out_refund")
            and self.state == "posted"
        )

    def _create_partner_ledger_if_needed(self, paid_at=None):
        Ledger = self.env["partner.attribution.ledger"].sudo()

        for move in self:
            if not move._should_create_partner_ledger():
                continue

            existing = Ledger.search_count([
                ("invoice_id", "=", move.id),
                ("entry_type", "in", ["invoice", "refund"]),
            ])
            if existing > 0:
                continue

            entry_type = "refund" if move.move_type == "out_refund" else "invoice"
            origin = move.reversed_entry_id if entry_type == "refund" else False

            Ledger.create({
                "company_id": move.company_id.id,
                "partner_id": move.attributed_partner_id.id,
                "invoice_id": move.id,
                "origin_invoice_id": origin.id if origin else False,
                "entry_type": entry_type,
                "commission_rate_used": float(move.commission_rate_used or 0.0),
                "commission_amount": float(move.commission_amount or 0.0),
                "state": "on_hold",
                "hold_reason": False,
                "invoice_paid_at": paid_at or fields.Datetime.now(),
            })

    # ----------------------------
    # Lead Reward rules (Lead Partner workflow)
    # ----------------------------
    def _create_lead_reward_if_needed(self, paid_at=None):
        Ledger = self.env["partner.attribution.ledger"].sudo()

        for move in self:
            if not move.attributed_partner_id:
                continue

            partner = move.attributed_partner_id.commercial_partner_id.sudo()
            if getattr(partner, "partner_role", False) != "lead":
                continue

            # Ledger creates immediately upon posting.
            if not (move.move_type == "out_invoice" and move.state == "posted"):
                continue

            if Ledger.search_count([("invoice_id", "=", move.id), ("entry_type", "=", "lead_reward")]) > 0:
                continue

            reward = float(getattr(partner, "lead_reward_amount", 0.0) or 0.0)
            if reward <= 0.0:
                continue

            Ledger.create({
                "company_id": move.company_id.id,
                "partner_id": partner.id,
                "invoice_id": move.id,
                "origin_invoice_id": False,
                "entry_type": "lead_reward",
                "commission_rate_used": 0.0,
                "commission_amount": reward,
                "state": "on_hold",
                "hold_reason": False,
                "invoice_paid_at": paid_at or fields.Datetime.now(),
            })

    def _pa_v1_process_ledger(self):
        if self.env.context.get("pa_v1_processing"):
            return

        self = self.with_context(pa_v1_processing=True)
        Ledger = self.env["partner.attribution.ledger"].sudo()

        for move in self:
            # 1. Update Customer Ledger lines
            if move.move_type in ('out_invoice', 'out_refund'):
                move._create_partner_ledger_if_needed()
                move._create_lead_reward_if_needed()
                ledger_lines = Ledger.search([("invoice_id", "=", move.id)])
                if ledger_lines:
                    ledger_lines.action_recompute_payout_state()

            # 2. Update Vendor Bill Ledger lines (THE CASCADE TRIGGER)
            if move.move_type == 'in_invoice':
                vendor_lines = Ledger.search([("vendor_bill_id", "=", move.id)])
                if vendor_lines:
                    vendor_lines.action_recompute_payout_state()
                    for batch in vendor_lines.mapped("payout_batch_id"):
                        batch.action_sync_paid_status()

        return True

    def write(self, vals):
        vals_dict = dict(vals)
        locked_fields = {"attributed_partner_id", "attribution_locked", "attribution_locked_at", "attribution_locked_by"}

        for move in self:
            if "attributed_partner_id" in vals_dict and move.state != "draft":
                raise UserError(_("You can only change Attributed Partner while the invoice is in Draft."))
            if move.attribution_locked and locked_fields.intersection(vals_dict.keys()):
                raise UserError(_("Invoice attribution is locked and cannot be changed."))

        res = super().write(vals)

        # THE INSTANT TRIGGER: Catches when money moves via standard write methods
        if "payment_state" in vals_dict:
            Ledger = self.env["partner.attribution.ledger"].sudo()
            
            # Customer Pays Invoice -> Instantly unlock commission
            customer_ledgers = Ledger.search([("invoice_id", "in", self.ids)])
            if customer_ledgers:
                customer_ledgers.action_recompute_payout_state()
                
            # You Pay Vendor Bill -> Instantly mark Batch as Done
            vendor_ledgers = Ledger.search([("vendor_bill_id", "in", self.ids)])
            if vendor_ledgers:
                vendor_ledgers.action_recompute_payout_state()
                for batch in vendor_ledgers.mapped("payout_batch_id"):
                    batch.action_sync_paid_status()

        return res

    def action_post(self):
        res = super().action_post()

        to_lock = self.filtered(lambda m: m.state == "posted" and m.attributed_partner_id and not m.attribution_locked)
        if to_lock:
            to_lock._lock_attribution()

        # Generate the ledger line immediately upon post
        to_process = self.filtered(lambda m: m.state == "posted")
        if to_process:
            to_process._pa_v1_process_ledger()

        return res
