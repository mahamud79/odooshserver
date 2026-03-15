# -*- coding: utf-8 -*-
import base64
import logging
import os
import re

from odoo import http, _
from odoo.http import request
from .common import ROLE_MAP

_logger = logging.getLogger(__name__)

def _safe_filename(name: str) -> str:
    name = (name or "document").strip()
    name = name.replace("\x00", "")
    name = name.replace("\n", " ").replace("\r", " ")
    name = os.path.basename(name)
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" ._")
    return name or "document"

class PartnerPortalMergedController(http.Controller):

    def _ctx_partner_ids(self):
        # FIX 1: Added .sudo() to prevent portal read-access crashes on commercial_partner_id
        partner = request.env.user.partner_id.sudo()
        commercial = partner.commercial_partner_id or partner
        partner_ids = list(set([partner.id, commercial.id]))
        return partner, commercial, partner_ids

    def _role(self):
        partner = request.env.user.partner_id.sudo()
        return getattr(partner, "partner_role", False) or False

    @http.route("/partners/portal", type="http", auth="user", website=True, sitemap=True)
    def partners_portal(self, **kwargs):
        user = request.env.user
        if user._is_public() or not user.has_group("base.group_portal"):
            return request.render("partner_attribution_v1.partner_portal_public_landing", {})

        partner, commercial, partner_ids = self._ctx_partner_ids()
        role = self._role()
        role_label = ROLE_MAP.get(role, {}).get("name", "")

        Ledger = request.env["partner.attribution.ledger"].sudo()
        lines = Ledger.search([("partner_id", "in", partner_ids)])
        
        payable_amount = sum(lines.filtered(lambda l: l.state == "payable").mapped("commission_amount"))
        paid_amount = sum(lines.filtered(lambda l: l.state == "paid").mapped("commission_amount"))
        on_hold_amount = sum(lines.filtered(lambda l: l.state == "on_hold").mapped("commission_amount"))

        # Added sudo() to ensure dashboard always loads documents safely
        Attachment = request.env["ir.attachment"].sudo()
        partner_docs = Attachment.search(
            [("res_model", "=", "res.partner"), ("res_id", "in", partner_ids)],
            order="id desc", limit=50
        )

        return request.render("partner_attribution_v1.portal_partner_dashboard", {
            "partner": partner,
            "commercial": commercial,
            "role": role,
            "role_label": role_label,
            "payable_amount": payable_amount,
            "paid_amount": paid_amount,
            "on_hold_amount": on_hold_amount,
            "partner_docs": partner_docs,
            "success_message": kwargs.get('success_message')
        })

    @http.route(["/audit/leads", "/partners/portal/leads"], type="http", auth="user", website=True)
    def audit_leads_form(self, **kw):
        _, commercial, partner_ids = self._ctx_partner_ids()
        leads = request.env['crm.lead'].sudo().search([('attributed_partner_id', 'in', partner_ids)], order="create_date desc")
        return request.render("partner_attribution_v1.portal_partner_leads", {'leads': leads})

    @http.route(["/audit/leads/submit", "/partners/portal/leads/submit"], type="http", auth="user", methods=["POST"], website=True, csrf=True)
    def audit_leads_submit(self, **post):
        try:
            user_partner = request.env.user.partner_id.sudo()
            commercial = user_partner.commercial_partner_id or user_partner
            
            request.env['crm.lead'].sudo().create({
                'name': post.get("name"),
                'contact_name': post.get("contact_name"),
                'type': 'opportunity',
                'attributed_partner_id': commercial.id,
                'email_from': post.get("email_from"),
                'phone': post.get("phone"),
                'description': post.get("description"),
                'company_id': commercial.company_id.id or request.env.company.id,
            })
            
            return request.redirect("/partners/portal?success_message=Opportunity created successfully!")
        except Exception as e:
            _logger.error("Lead submission failed: %s", str(e))
            return request.redirect("/partners/portal?error_message=An error occurred while saving the lead.")
            
    @http.route("/partners/portal/orders", type="http", auth="user", website=True)
    def portal_orders(self, **kw):
        role = self._role()
        if role not in ("ap", "sales_agent", "sales_partner"):
            return request.redirect("/partners/portal")

        _, commercial, partner_ids = self._ctx_partner_ids()
        SO = request.env["sale.order"].sudo()
        domain = [("partner_id", "in", partner_ids)]
        if "attributed_partner_id" in SO._fields:
            domain = ["|", ("attributed_partner_id", "in", partner_ids)] + domain
            
        orders = SO.search(domain, order="id desc", limit=100)

        return request.render("partner_attribution_v1.portal_partner_orders", {
            "orders": orders,
            "role": role,
            "role_label": ROLE_MAP.get(role, {}).get("name", ""),
        })

    @http.route("/partners/portal/commissions", type="http", auth="user", website=True)
    def portal_commissions(self, **kw):
        partner, commercial, partner_ids = self._ctx_partner_ids()
        Ledger = request.env["partner.attribution.ledger"].sudo()
        lines = Ledger.search([("partner_id", "in", partner_ids)], order="id desc")
        
        return request.render("partner_attribution_v1.portal_partner_commissions", {
            "partner": partner,
            "lines": lines,
            "payable_amount": sum(lines.filtered(lambda l: l.state == "payable").mapped("commission_amount")),
            "paid_amount": sum(lines.filtered(lambda l: l.state == "paid").mapped("commission_amount")),
            "on_hold_amount": sum(lines.filtered(lambda l: l.state == "on_hold").mapped("commission_amount")),
        })

    @http.route("/partners/portal/pricelist", type="http", auth="user", website=True)
    def portal_pricelist(self, **kw):
        role = self._role()
        if role != "sales_partner":
            return request.redirect("/partners/portal")

        partner, commercial, _ = self._ctx_partner_ids()
        pricelist = getattr(partner, "property_product_pricelist", False)
        items = request.env["product.pricelist.item"].sudo().search([("pricelist_id", "=", pricelist.id)]) if pricelist else []

        return request.render("partner_attribution_v1.portal_partner_pricelist", {
            "partner": partner,
            "pricelist": pricelist,
            "items": items,
        })

    @http.route(["/partners/portal/documents/upload"], type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def partners_portal_documents_upload(self, **post):
        _, commercial, _ = self._ctx_partner_ids()
        
        upload = post.get("document") or request.httprequest.files.get("document")
        
        if upload and getattr(upload, 'filename', False):
            allowed_mimetypes = ['application/pdf', 'image/jpeg', 'image/jpg', 'image/png']
            if upload.mimetype not in allowed_mimetypes:
                return request.redirect("/partners/portal?error_message=Invalid file format. Only PDF, JPG, and PNG files are allowed.")

            content = upload.read()
            if len(content) > 10 * 1024 * 1024:
                return request.redirect("/partners/portal?error_message=File exceeds the maximum allowed size of 10MB.")

            # FIX 2: THE 2-STEP ATTACHMENT BYPASS
            # Creates the file unattached to bypass the Chatter crash, then links it to the partner.
            attachment = request.env["ir.attachment"].sudo().create({
                "name": _safe_filename(upload.filename),
                "type": "binary",
                "datas": base64.b64encode(content),
                "mimetype": upload.mimetype,
            })
            
            attachment.sudo().write({
                "res_model": "res.partner",
                "res_id": commercial.id,
            })
            
        referer = request.httprequest.headers.get('Referer', '/partners/portal')
        return request.redirect(referer)
