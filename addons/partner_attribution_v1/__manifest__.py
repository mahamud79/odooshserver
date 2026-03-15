# -*- coding: utf-8 -*-
{
    "name": "Partner Attribution v1",
    "version": "17.0.1.0.0",
    "category": "Sales",
    "summary": "Manual partner-code attribution stored permanently, propagated to invoices, ledger + payout automation.",
    "license": "LGPL-3",
    "application": False,
    "installable": True,


    "depends": [
        "base",
        "mail",
        "contacts",

        # Sales / CRM
        "sale_management",
        "crm",
        "sale_crm",

        # Portal / website
        "portal",
        "website",
        "auth_signup",

        # Accounting (you touch account.move)
        "account",

        # Only keep if your portal shows pricelist/products
        "product",
    ],

    "data": [
        # Security
        "security/security_groups.xml",
        "security/ir.model.access.csv",
        "security/record_rules.xml",

        # Sequences / cron
        "data/ir_sequence.xml",
        "data/ir_sequence_inquiry.xml",
        "data/ir_sequence_payout_batch.xml",


        # Ledger views (before menus)
        "views/partner_attribution_ledger_views.xml",

        # Backend views
        "views/res_partner_views.xml",
        "views/sale_order_views.xml",
        "views/account_move_views.xml",
        "views/attribution_search_views.xml",
        "views/payout_batch_views.xml",
        "views/partner_contract_views.xml",
        "views/partner_inquiry_views.xml",

        # Portal / Website
        "views/portal_partner_pages.xml",
        "views/portal_partner_orders.xml",
        "views/portal_partner_pricelist.xml",
        "views/portal_partner_commissions.xml",
        "views/portal_partner_leads.xml",
        "views/portal_partner_contracts.xml",
        "views/portal_partner_kyc.xml",
        "views/website_partner_pages.xml",

        # Reports
        "views/invoice_report_inherit.xml",
        "views/partner_contract_report.xml",
        "views/report_invoice.xml",

        # Menus last
        "views/menus.xml",
    ],

    # IMPORTANT: keep CRM stages OUT of data to avoid breaking Odoo core tests.
    "demo": [
        "demo/crm_partner_application_stages.xml",
    ],
    
    "assets": {},
}
