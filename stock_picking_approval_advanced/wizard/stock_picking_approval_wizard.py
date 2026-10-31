# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class StockPickingApprovalWizard(models.TransientModel):
    _name = 'stock.picking.approval.wizard'
    _description = 'Transfer Approval Decision'

    picking_id = fields.Many2one('stock.picking', string='Transfer', required=True, readonly=True)
    action = fields.Selection(
        [('approve', 'Approve'), ('reject', 'Reject')], required=True, default='approve', readonly=True)
    tier_name = fields.Char(string='Tier', compute='_compute_tier_info')
    require_signature = fields.Boolean(compute='_compute_tier_info')
    comment = fields.Text(string='Comment')
    rejection_reason = fields.Text(string='Rejection Reason')
    signature = fields.Image(string='Signature', max_width=1024, max_height=1024)

    @api.depends('picking_id')
    def _compute_tier_info(self):
        for wizard in self:
            wizard.tier_name = wizard.picking_id.approval_current_line_id.name
            wizard.require_signature = wizard.picking_id.approval_rule_id.require_signature

    def action_confirm(self):
        self.ensure_one()
        picking = self.picking_id
        if self.action == 'reject':
            if not (self.rejection_reason or '').strip():
                raise UserError(_("Please provide the reason of the rejection."))
            picking._approval_do_reject(self.rejection_reason.strip())
            return {'type': 'ir.actions.act_window_close'}
        result = picking._approval_do_approve(comment=self.comment, signature=self.signature)
        if isinstance(result, dict):
            return result
        return {'type': 'ir.actions.act_window_close'}
