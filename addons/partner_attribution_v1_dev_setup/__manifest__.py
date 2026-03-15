# -*- coding: utf-8 -*-
{
    "name": "Partner Attribution V1 Dev Setup (DEV ONLY)",
    "version": "17.0.1.0.0",
    "summary": "Dev-only users and sample data for testing. Do NOT merge to production.",
    "category": "Tools",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "auto_install": True,

    "depends": [
        "partner_attribution_v1",
    ],

    "data": [
        "data/dev_users.xml",
    ],
    "demo": [
        "demo/sample_partners.xml",
    ],
}
