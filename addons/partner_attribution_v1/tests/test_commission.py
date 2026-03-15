# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError

class TestPartnerCommission(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        
        # Setup Test Partner (Affiliate)
        cls.partner_ap = cls.env['res.partner'].create({
            'name': 'Test Affiliate',
            'partner_role': 'ap',
            'partner_state': 'approved',
            'commission_rate': 10.0,
            'kyc_status': 'verified',
            'bank_verified': True,
        })
        
        # Setup Product
        cls.product = cls.env['product.product'].create({
            'name': 'Test Service',
            'type': 'service',
            'list_price': 1000.0,
        })

    def test_01_full_commission_flow(self):
        """Test: Invoice Post -> Ledger Creation -> Payment -> Payable State"""
        # 1. Create Customer Invoice
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.env.ref('base.res_partner_12').id,
            'attributed_partner_id': self.partner_ap.id,
            'invoice_line_ids': [(0, 0, {
                'product_id': self.product.id,
                'quantity': 1,
                'price_unit': 1000.0,
            })],
        })
        invoice.action_post()
        
        # 2. Verify Ledger Entry Created & On Hold
        ledger = self.env['partner.attribution.ledger'].search([('invoice_id', '=', invoice.id)])
        self.assertTrue(ledger, "Ledger entry should be created on invoice post")
        self.assertEqual(ledger.state, 'on_hold', "Unpaid invoices must leave ledger on_hold")
        self.assertEqual(ledger.commission_amount, 100.0, "Commission should be 10% of 1000")

    def test_02_kyc_blocking(self):
        """Test: Unverified partner ledgers stay on hold even if paid"""
        self.partner_ap.kyc_status = 'pending' # Remove KYC
        
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.env.ref('base.res_partner_12').id,
            'attributed_partner_id': self.partner_ap.id,
            'invoice_line_ids': [(0, 0, {'product_id': self.product.id, 'price_unit': 500.0})],
        })
        invoice.action_post()
        
        # Force payment state
        invoice.payment_state = 'paid'
        invoice.flush_recordset()
        
        ledger = self.env['partner.attribution.ledger'].search([('invoice_id', '=', invoice.id)])
        ledger.action_recompute_payout_state()
        
        self.assertEqual(ledger.state, 'on_hold', "Ledger MUST remain on_hold if KYC is missing")

    def test_03_attribution_lock(self):
        """Test: Cannot change attribution after invoice is posted"""
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.env.ref('base.res_partner_12').id,
            'attributed_partner_id': self.partner_ap.id,
            'invoice_line_ids': [(0, 0, {'product_id': self.product.id, 'price_unit': 100.0})],
        })
        invoice.action_post()
        
        with self.assertRaises(UserError):
            invoice.write({'attributed_partner_id': False})
