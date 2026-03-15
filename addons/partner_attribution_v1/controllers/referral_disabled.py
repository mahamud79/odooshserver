# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request

class PartnerReferralController(http.Controller):

    @http.route('/r/<code>', type='http', auth='public', website=True)
    def partner_referral_redirect(self, code, **kw):
        """
        Catches legacy or attempted referral links.
        Cookie/session tracking is strictly disabled by design.
        Redirects safely to the portal.
        """
        return request.redirect('/partners/portal')
