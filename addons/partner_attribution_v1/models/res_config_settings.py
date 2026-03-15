from odoo import fields, models, api

class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    extract_single_line_per_tax = fields.Boolean(
        string="Single Line Per Tax",
        default=False,
    )

    def _register_hook(self):
        """
        This hook runs automatically when the Odoo server starts. 
        It safely searches the database for the poisoned 'disabled' values 
        left over from the old configuration and resets them to the Odoo standard 'no_send'.
        """
        super()._register_hook()
        try:
            # We use a savepoint so if the column doesn't exist, it fails gracefully without crashing the server
            with self.env.cr.savepoint():
                self.env.cr.execute("""
                    UPDATE res_company 
                    SET extract_in_invoice_digitalization_mode = 'no_send' 
                    WHERE extract_in_invoice_digitalization_mode = 'disabled'
                """)
                self.env.cr.execute("""
                    UPDATE res_company 
                    SET extract_out_invoice_digitalization_mode = 'no_send' 
                    WHERE extract_out_invoice_digitalization_mode = 'disabled'
                """)
        except Exception:
            pass
