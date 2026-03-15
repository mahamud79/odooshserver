# -*- coding: utf-8 -*-
from odoo import api, fields, models


class CrmLead(models.Model):
    _inherit = "crm.lead"

    attributed_partner_id = fields.Many2one(
        "res.partner",
        string="Attributed Partner",
        index=True,
        copy=False,
        help="Partner (portal user) who submitted this lead.",
    )

    partner_code = fields.Char(
        string="Partner Code",
        index=True,
        copy=False,
        help="Snapshot of partner_code at lead creation for audit (codes-only).",
    )

    def _prepare_sale_order_values(self, partner):
        """
        When converting a Lead into a Quotation/Sale Order,
        propagate partner attribution (Lead Partner workflow).
        """
        vals = super()._prepare_sale_order_values(partner)

        if self.attributed_partner_id:
            vals.update({
                "attributed_partner_id": self.attributed_partner_id.id,
                "partner_code_input": self.partner_code or self.attributed_partner_id.partner_code or False,
            })
        return vals
