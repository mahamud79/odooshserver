# -*- coding: utf-8 -*-
from odoo import http, _
from odoo.http import request


class PartnerPortalContractsController(http.Controller):

    def _get_partner_ctx(self):
        partner = request.env.user.partner_id
        if not partner:
            return None, None
        commercial = partner.commercial_partner_id or partner
        return partner, commercial

    @http.route("/partners/portal/contracts", type="http", auth="user", website=True, sitemap=False)
    def portal_contracts(self, **kw):
        partner, commercial = self._get_partner_ctx()
        if not partner:
            return request.redirect("/web/login")

        # record rules apply (no sudo) for partner fields
        template = getattr(commercial, "contract_template_id", False)
        acceptance = getattr(commercial, "contract_acceptance_id", False)

        return request.render("partner_attribution_v1.portal_partner_contracts", {
            "partner": partner,
            "commercial": commercial,
            "template": template,
            "acceptance": acceptance,
        })

    @http.route("/partners/portal/contracts/download", type="http", auth="user", website=True, sitemap=False)
    def portal_contracts_download(self, **kw):
        """
        Portal-safe download:
        - verifies current user has an assigned template
        - redirects to /web/content with access_token ensured
        """
        partner, commercial = self._get_partner_ctx()
        if not partner:
            return request.redirect("/web/login")

        template = getattr(commercial, "contract_template_id", False)
        if not template or not template.attachment_id:
            return request.redirect("/partners/portal/contracts")

        att = template.attachment_id.sudo()

        # Ensure token exists (portal-safe downloads)
        if hasattr(att, "_ensure_access_token"):
            att._ensure_access_token()
        elif "access_token" in att._fields and not att.access_token:
            # fallback (rare)
            import secrets
            att.write({"access_token": secrets.token_urlsafe(24)})

        token_q = ""
        if "access_token" in att._fields and att.access_token:
            token_q = "&access_token=%s" % att.access_token

        return request.redirect("/web/content/%s?download=true%s" % (att.id, token_q))

    @http.route("/partners/portal/contracts/accept", type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def portal_contracts_accept(self, **post):
        partner, commercial = self._get_partner_ctx()
        if not partner:
            return request.redirect("/web/login")

        template = getattr(commercial, "contract_template_id", False)
        if not template or not template.attachment_id:
            return request.redirect("/partners/portal/contracts")

        acceptance = getattr(commercial, "contract_acceptance_id", False)
        if acceptance and acceptance.template_id and acceptance.template_id.id == template.id:
            return request.redirect("/partners/portal/contracts")

        # capture IP (best effort)
        ip = False
        try:
            ip = request.httprequest.remote_addr
        except Exception:
            ip = False

        # Create acceptance snapshot using your model method
        if hasattr(commercial, "_create_acceptance_snapshot"):
            commercial.sudo()._create_acceptance_snapshot(template.sudo(), accepted_ip=ip or False)

        return request.redirect("/partners/portal/contracts")