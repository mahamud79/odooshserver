# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError, UserError

class SaleOrder(models.Model):
    _inherit = "sale.order"

    # IMPORTANT: codes-only attribution as per spec (no cookie/session) 
    partner_code_input = fields.Char(string="Partner Code", copy=False)

    attributed_partner_id = fields.Many2one(
        "res.partner",
        string="Attributed Partner",
        copy=False,
        index=True,
        domain=[("partner_state", "=", "approved")],
    )

    attribution_locked = fields.Boolean(string="Attribution Locked", default=False, copy=False)
    attribution_locked_at = fields.Datetime(string="Attribution Locked At", copy=False, readonly=True)
    attribution_locked_by = fields.Many2one("res.users", string="Locked By", copy=False, readonly=True)

    # ----------------------------
    # Helpers
    # ----------------------------
    def _find_partner_by_code(self, code):
        """Lookup an approved partner by code. sudo() for portal/website contexts."""
        code = (code or "").strip()
        if not code:
            return self.env["res.partner"]
        return self.env["res.partner"].sudo().search(
            [("partner_code", "=", code), ("partner_state", "=", "approved")],
            limit=1,
        )

    def _sync_code_from_attributed_partner(self, vals):
        """
        If user sets attributed_partner_id (and did not set partner_code_input),
        mirror the partner_code into partner_code_input.
        """
        if "attributed_partner_id" in vals and "partner_code_input" not in vals:
            partner = (
                self.env["res.partner"].browse(vals["attributed_partner_id"])
                if vals.get("attributed_partner_id")
                else self.env["res.partner"]
            )
            vals["partner_code_input"] = partner.partner_code or False

    def _sync_attributed_partner_from_code(self, vals):
        """
        If user sets partner_code_input (and did not set attributed_partner_id),
        resolve partner_code_input -> attributed_partner_id.
        """
        if "partner_code_input" in vals and "attributed_partner_id" not in vals:
            code = (vals.get("partner_code_input") or "").strip()
            if not code:
                vals["attributed_partner_id"] = False
                return
            partner = self._find_partner_by_code(code)
            vals["attributed_partner_id"] = partner.id if partner else False

    # ----------------------------
    # Buttons
    # ----------------------------
    def action_lock_attribution(self):
        for order in self:
            if order.attribution_locked:
                continue
            order.with_context(bypass_attribution_lock=True).write({
                "attribution_locked": True,
                "attribution_locked_at": fields.Datetime.now(),
                "attribution_locked_by": self.env.user.id,
            })
        return True

    def action_unlock_attribution(self):
        if not self.env.user.has_group("sales_team.group_sale_manager"):
            raise ValidationError(_("Only Sales Managers can unlock attribution."))

        for order in self:
            if not order.attribution_locked:
                continue
            order.with_context(bypass_attribution_lock=True).write({
                "attribution_locked": False,
                "attribution_locked_at": False,
                "attribution_locked_by": False,
            })
        return True

    # ----------------------------
    # UI onchange (NO write recursion)
    # ----------------------------
    @api.onchange("partner_code_input")
    def _onchange_partner_code_input(self):
        for order in self:
            code = (order.partner_code_input or "").strip()
            if not code:
                order.attributed_partner_id = False
                return

            partner = order._find_partner_by_code(code)
            if partner:
                order.attributed_partner_id = partner
            else:
                order.attributed_partner_id = False
                return {
                    "warning": {
                        "title": _("Partner not found / not approved"),
                        "message": _("No approved partner found for code: %s") % code,
                    }
                }

    @api.onchange("attributed_partner_id")
    def _onchange_attributed_partner_id_set_code(self):
        for order in self:
            order.partner_code_input = order.attributed_partner_id.partner_code if order.attributed_partner_id else False

    # ----------------------------
    # Create / Write (Fixed method calls)
    # ----------------------------
    @api.model_create_multi
    def create(self, vals_list):
        new_vals_list = []
        for vals in vals_list:
            vals = dict(vals)

            # Sync attribution fields using correct helper names 
            self._sync_attributed_partner_from_code(vals)
            self._sync_code_from_attributed_partner(vals)

            if vals.get("attribution_locked"):
                vals.setdefault("attribution_locked_at", fields.Datetime.now())
                vals.setdefault("attribution_locked_by", self.env.user.id)

            new_vals_list.append(vals)

        return super().create(new_vals_list)

    def write(self, vals):
        if not vals:
            return super().write(vals)

        # Lock protection (Critical for audit-safe ledger) 
        sensitive_fields = {"partner_code_input", "attributed_partner_id"}
        if sensitive_fields.intersection(vals.keys()) and not self.env.context.get("bypass_attribution_lock"):
            if self.filtered("attribution_locked"):
                raise UserError(_("Attribution is locked and cannot be changed."))

        # Lock metadata maintenance
        vals = dict(vals)
        if "attribution_locked" in vals:
            if vals.get("attribution_locked"):
                vals.setdefault("attribution_locked_at", fields.Datetime.now())
                vals.setdefault("attribution_locked_by", self.env.user.id)
            else:
                vals.update({
                    "attribution_locked_at": False,
                    "attribution_locked_by": False,
                })

        # Corrected method calls to existing helpers 
        if "partner_code_input" in vals:
            self._sync_attributed_partner_from_code(vals)
        if "attributed_partner_id" in vals:
            self._sync_code_from_attributed_partner(vals)

        return super().write(vals)

    def _prepare_invoice(self):
        """Pass attribution data to invoice for commission engine."""
        vals = super()._prepare_invoice()

        if self.attributed_partner_id:
            locked_by = self.attribution_locked_by.id if self.attribution_locked_by else self.env.user.id
            vals.update({
                "attributed_partner_id": self.attributed_partner_id.id,
                "attribution_locked": True,
                "attribution_locked_at": self.attribution_locked_at or fields.Datetime.now(),
                "attribution_locked_by": locked_by,
            })

        return vals
