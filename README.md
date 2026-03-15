# Odoo Partner Attribution & Automation (v1)

[![Odoo Version](https://img.shields.io/badge/Odoo-17.0%20%7C%2018.0-714B67.svg)](https://www.odoo.com/)
[![License: LGPL-3](https://img.shields.io/badge/License-LGPL--3-blue.svg)](https://www.gnu.org/licenses/lgpl-3.0-standalone.html)

A high-integrity, accounting-first partner management system for Odoo Enterprise. This module automates the lifecycle of Affiliate Partners, Lead Partners, Sales Agents, and Sales Partners (Buy-Sell) through a strict codes-only attribution model.

## 🚀 Key Value Propositions

* **Zero-Cookie Attribution**: Enforces "codes-only" tracking to meet strict privacy and technical requirements—no reliance on sessions or hidden cookies.
* **Audit-Safe Finance**: Percentage commissions and fixed lead rewards are safely triggered *only* when customer invoices reach the `In Payment` or `Paid` state.
* **KYC Payout Gating**: Automatically blocks the generation of vendor bills and holds ledger lines until the partner's bank, VAT, and company details are fully verified.
* **Organic & Referred Onboarding**: Partners can apply organically without a code, or be linked automatically via an optional referral code during sign-up.

## 🛠 Project Architecture

| Component | Responsibility |
| :--- | :--- |
| **Logic Engine** | `res_partner.py`, `account_move.py`, `partner_attribution_ledger.py` |
| **Automation** | `payout_batch.py`, `account_move_line.py`, `crm_lead.py` |
| **Frontend** | `partner_website.py`, `partner_kyc_portal.py`, `portal_partner_pages.xml` |
| **Security & QA** | `security_groups.xml`, `record_rules.xml`, `test_commission.py` |

## 📥 Installation

1.  **Deploy** to your Odoo.sh or local instance.
2.  **Install** via the Apps menu.
3.  **Version Note**: The `__manifest__.py` is currently set to `18.0.1.0.0` for Odoo.sh staging compatibility, but the architecture is strictly built to support Odoo `17.0.1.0.0` for production deployment if required.
4.  **Security**: Assign internal users to the `Partner Attribution: Manager` group to enable application approval workflows and payout batch generation.

## 🔄 Standard Workflow

1.  **Apply**: Prospective partners apply via `/partners/apply` (referral code is optional).
2.  **Approve**: Management reviews the inquiry and clicks **Approve** on the partner form to generate an immutable Partner Code.
3.  **Onboard**: Admin clicks **Invite Link**; the system creates a Portal User and sends an activation email.
4.  **Operate**: Partners submit leads or orders via the portal. Sales Partners access their discounted pricelists. 
5.  **Automated Ledger**: When a customer pays an invoice, a commission ledger entry is automatically generated in the background.
6.  **Payout**: Managers manually batch "Payable" ledger lines into official Odoo Vendor Bills, ensuring the company maintains full control over cash flow.

## ⚖️ Audit Compliance

This module was strictly built to pass 3rd-party architectural audits. It maintains a hard separation between customer revenue and partner liability, utilizes an append-only ledger system, never auto-pays external accounts, and provides a fully traceable audit trail from initial lead to final vendor bill.

---
*Developed for professional Odoo.sh environments.*
