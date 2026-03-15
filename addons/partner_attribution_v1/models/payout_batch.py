# -*- coding: utf-8 -*-
import base64
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class PartnerAttributionPayoutBatch(models.Model):
    _name = "partner.attribution.payout.batch"
    _description = "Partner Payout Batch"
    _order = "id desc"
    _rec_name = "name"

    name = fields.Char(default=lambda self: _("New"), required=True, copy=False)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id", readonly=True)

    state = fields.Selection(
        [("draft", "Draft"), ("generated", "Vendor Bills Generated"), ("done", "Done")],
        default="draft",
        required=True,
        index=True,
    )

    ledger_line_ids = fields.One2many(
        "partner.attribution.ledger",
        "payout_batch_id",
        string="Ledger Lines",
        readonly=True,
        copy=False,
    )
    

    vendor_bill_ids = fields.One2many(
        "account.move",
        "partner_payout_batch_id",
        string="Vendor Bills",
        readonly=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name") in (False, _("New"), "New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("partner.attribution.payout.batch") or _("New")
        return super().create(vals_list)


    #
    
    def _get_vendor_bill_journal(self, company):
        param = self.env["ir.config_parameter"].sudo().get_param("partner_attribution_v1.vendor_bill_journal_id")
        if param:
            journal = self.env["account.journal"].browse(int(param))
            if journal.exists() and journal.company_id == company:
                return journal

        return self.env["account.journal"].sudo().search(
            [("company_id", "=", company.id), ("type", "=", "purchase")],
            limit=1,
        )

    def _get_expense_account(self, company):
        # FIX: Added .with_company(company) to bypass cross-company security blocks
        Account = self.env["account.account"].sudo().with_company(company)
        
        domain = [("deprecated", "=", False), ("account_type", "in", ("expense", "expense_direct_cost"))]
        if "company_ids" in Account._fields:
            domain.append(("company_ids", "in", company.ids))
        else:
            domain.append(("company_id", "=", company.id))
            
        acc = Account.search(domain, limit=1)
        
        if not acc:
            try:
                code = "610000"
                while Account.search([("code", "=", code)]):
                    code = str(int(code) + 1)
                    
                vals = {"name": "Partner Commissions", "code": code, "account_type": "expense"}
                if "company_ids" in Account._fields:
                    vals["company_ids"] = [(6, 0, company.ids)]
                else:
                    vals["company_id"] = company.id
                acc = Account.create(vals)
            except Exception as e:
                # FIX: Print the exact Odoo core error if auto-create fails again
                raise UserError(_(
                    "Auto-creation of the Expense account failed!\n\n"
                    "Odoo System Error: %s\n\n"
                    "MANUAL FIX:\n"
                    "1. Switch your active company to '%s' in the top right corner of Odoo.\n"
                    "2. Go to Accounting -> Configuration -> Chart of Accounts\n"
                    "3. Create at least one account with Type = Expense."
                ) % (str(e), company.name))
                
        return acc

    def _precheck_vendor_bill_config(self, partner, company, journal):
        if not journal:
            raise UserError(_("No Purchase Journal found for company %s.") % company.name)

        # 1. Evaluate the partner strictly in the context of the target company
        partner_in_company = partner.sudo().with_company(company)
        payable = partner_in_company.property_account_payable_id

        # 2. Auto-Assign a Payable Account if missing for this company
        if not payable:
            Account = self.env["account.account"].sudo().with_company(company)
            domain = [("deprecated", "=", False), ("account_type", "=", "liability_payable")]
            if "company_ids" in Account._fields:
                domain.append(("company_ids", "in", company.ids))
            else:
                domain.append(("company_id", "=", company.id))
                
            fallback_payable = Account.search(domain, limit=1)
            
            if fallback_payable:
                partner_in_company.property_account_payable_id = fallback_payable
                
                # ---> THE MAGIC FIX <---
                # Force Odoo to save the new account to the database and clear its 
                # short-term memory so the Vendor Bill creation actually sees it!
                self.env.flush_all()
                self.env.invalidate_all()
                
            else:
                raise UserError(_(
                    "Cannot create Vendor Bill! The system could not find an 'Accounts Payable' "
                    "account for company '%s'.\n\n"
                    "Fix:\n"
                    "1. Switch to company '%s' in the top right corner.\n"
                    "2. Go to Accounting -> Configuration -> Chart of Accounts\n"
                    "3. Create an account with the Type set to 'Payable'."
                ) % (company.name, company.name))

    # ----------------------------
    # Actions
    # ----------------------------
    def action_load_payables(self):
        Ledger = self.env["partner.attribution.ledger"].sudo()

        for batch in self:
            if batch.ledger_line_ids:
                batch.ledger_line_ids.sudo().write({"payout_batch_id": False})

            candidates = Ledger.search([
                ("company_id", "=", batch.company_id.id),
                ("entry_type", "in", ["invoice", "lead_reward"]),
                ("vendor_bill_id", "=", False),
                ("state", "in", ["on_hold", "payable"]),
                ("payout_batch_id", "=", False),
            ])

            if candidates:
                candidates.action_recompute_payout_state()

            payables = candidates.filtered(lambda l: l.state == "payable" and (l.commission_amount or 0.0) > 0.0)
            
            if payables:
                payables.sudo().write({"payout_batch_id": batch.id})
                continue

            on_hold = candidates.filtered(lambda l: l.state == "on_hold")
            already_billed = Ledger.search_count([
                ("company_id", "=", batch.company_id.id),
                ("entry_type", "in", ["invoice", "lead_reward"]),
                ("vendor_bill_id", "!=", False),
            ])

            raise UserError(_(
                "No PAYABLE ledger lines found for batch %s.\n\n"
                "Audit Summary:\n"
                "- Candidates checked: %s\n"
                "- Still ON HOLD (Compliance Gate): %s\n"
                "- Already billed elsewhere: %s\n\n"
                "Resolution Checklist:\n"
                "1. Confirm Customer Invoice is marked as PAID.\n"
                "2. Confirm Partner KYC Status is 'Verified'.\n"
                "3. Confirm 'Bank Verified' is checked on the Partner record.\n"
                "4. Confirm Commission Amount is greater than 0."
            ) % (batch.name, len(candidates), len(on_hold), already_billed))

        return True

    def action_generate_vendor_bills(self):
        self.ensure_one()
        if self.state != 'draft':
            return
            
        # Group by partner to create one bill per partner
        partner_data = {}
        for line in self.ledger_line_ids.filtered(lambda l: l.state == 'payable' and not l.vendor_bill_id):
            partner_data.setdefault(line.partner_id, []).append(line)

        if not partner_data:
            raise UserError(_("No PAYABLE ledger lines found for batch %s.") % self.name)

        batch = self
        company = batch.company_id
        journal = self.env['account.journal'].sudo().search([
            ('type', '=', 'purchase'),
            ('company_id', '=', company.id)
        ], limit=1)

        product = self.env['product.product'].sudo().with_company(company).search([
            ('default_code', '=', 'PARTNER_COMM'),
            '|', ('company_id', '=', False), ('company_id', '=', company.id)
        ], limit=1)
        
        if not product:
            product = self.env['product.product'].sudo().with_company(company).create({
                'name': 'Partner Commission',
                'type': 'service',
                'default_code': 'PARTNER_COMM',
                'purchase_ok': True,
                'sale_ok': False,
                'company_id': False,
            })
        Move = self.env["account.move"].sudo().with_company(company).with_context(allowed_company_ids=company.ids)

        for partner, lines in partner_data.items():
            # PHASE 1: Ensure Partner is ready for THIS company
            self._precheck_vendor_bill_config(partner, company, journal)
            
            # PHASE 2: Force Odoo to save the Partner/Account changes and wipe cache
            self.env.flush_all()
            self.env.invalidate_all()
            
            # PHASE 3: Fetch the fresh expense account
            expense_acc = self._get_expense_account(company)
            total = sum(l.commission_amount for l in lines)

            bill_vals = {
                "move_type": "in_invoice",
                "partner_id": partner.id,
                "company_id": company.id,
                "currency_id": batch.currency_id.id,
                "invoice_date": fields.Date.context_today(self),
                "ref": batch.name,
                "partner_payout_batch_id": batch.id,
                "journal_id": journal.id,
                "invoice_line_ids": [(0, 0, {
                    "product_id": product.id,
                    "name": _("Partner payout batch: %s") % batch.name,
                    "quantity": 1.0,
                    "price_unit": float(total),
                    "account_id": expense_acc.id,
                })],
            }
            
            try:
                bill = Move.create(bill_vals)
                bill.action_post()
                for line in lines:
                    line.write({'vendor_bill_id': bill.id})
            except Exception as e:
                raise UserError(_("Vendor bill for %s was created but failed to post.\nError: %s") % (partner.name, str(e)))
                
        self.state = 'generated'
        return True

    
    def action_sync_paid_status(self):
        for batch in self:
            if not batch.vendor_bill_ids:
                continue

            all_paid = True
            for bill in batch.vendor_bill_ids:
                lines = batch.ledger_line_ids.filtered(lambda l: l.partner_id == bill.partner_id)
                # Only accept paid or in_payment
                if getattr(bill, "payment_state", False) in ("paid", "in_payment"):
                    # Let the state machine decide
                    lines.action_recompute_payout_state()
                else:
                    all_paid = False

            # If everything is at least in payment, finish the batch
            if all_paid:
                batch.sudo().write({"state": "done"})

        return True

    @api.model
    def _cron_sync_payout_batches_paid_status(self):
        for company in self.env["res.company"].sudo().search([]):
            Batch = self.sudo().with_company(company)
            last_id = 0
            while True:
                batches = Batch.search(
                    [("state", "in", ("generated", "done")), ("id", ">", last_id)],
                    order="id asc",
                    limit=200,
                )
                if not batches:
                    break
                batches.action_sync_paid_status()
                last_id = batches[-1].id
        return True

    @api.model
    def _cron_recompute_orphan_ledger_states(self):
        if "partner.attribution.ledger" not in self.env:
            return True
        LedgerModel = self.env["partner.attribution.ledger"].sudo()
        for company in self.env["res.company"].sudo().search([]):
            last_id = 0
            while True:
                lines = LedgerModel.search([
                    ("company_id", "=", company.id),
                    ("entry_type", "in", ["invoice", "lead_reward"]),
                    ("vendor_bill_id", "=", False),
                    ("payout_batch_id", "=", False),
                    ("state", "in", ("on_hold", "payable")),
                    ("id", ">", last_id),
                ], order="id asc", limit=500)
                if not lines:
                    break
                lines.action_recompute_payout_state()
                last_id = lines[-1].id
        return True

    @api.model
    def _cron_auto_generate_payout_batches(self):
        """Automatically runs every night to scoop payables into batches and generate bills."""
        Ledger = self.env["partner.attribution.ledger"].sudo()
        
        for company in self.env["res.company"].sudo().search([]):
            # Find all payable lines not yet in a batch
            payables = Ledger.search([
                ("company_id", "=", company.id),
                ("state", "=", "payable"),
                ("payout_batch_id", "=", False),
                ("vendor_bill_id", "=", False)
            ])
            
            if not payables:
                continue
                
            # Create the automated batch
            batch = self.sudo().with_company(company).create({
                "company_id": company.id,
                "name": _("Auto-Batch: %s") % fields.Date.context_today(self)
            })
            
            # Load the payables and generate bills instantly
            payables.write({"payout_batch_id": batch.id})
            batch.action_generate_vendor_bills()
            
        return True
