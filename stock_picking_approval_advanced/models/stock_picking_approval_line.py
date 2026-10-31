# -*- coding: utf-8 -*-
from odoo import api, fields, models

APPROVAL_LINE_STATES = [
    ('waiting', 'Waiting'),
    ('pending', 'Pending'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
    ('cancelled', 'Cancelled'),
]


class StockPickingApprovalLine(models.Model):
    """One tier of one approval cycle of a transfer: the audit trail.

    Lines are only ever written by the approval engine (in sudo), never
    directly by users, so the sign-off history cannot be tampered with."""

    _name = 'stock.picking.approval.line'
    _description = 'Transfer Approval Sign-off'
    _order = 'picking_id, cycle desc, sequence, id'
    _rec_name = 'name'

    picking_id = fields.Many2one(
        'stock.picking', string='Transfer', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='picking_id.company_id', store=True, index=True)
    rule_id = fields.Many2one('stock.picking.approval.rule', string='Rule', ondelete='set null')
    tier_id = fields.Many2one('stock.picking.approval.tier', string='Tier Template', ondelete='set null')
    cycle = fields.Integer(string='Cycle', default=1, help="Approval round of the transfer.")
    sequence = fields.Integer(default=10)
    name = fields.Char(string='Tier', required=True)
    approver_user_ids = fields.Many2many(
        'res.users', 'stock_picking_approval_line_user_rel', 'line_id', 'user_id',
        string='Approvers')
    state = fields.Selection(APPROVAL_LINE_STATES, default='waiting', required=True, index=True)
    user_id = fields.Many2one('res.users', string='Signed By')
    date = fields.Datetime(string='Decision Date')
    comment = fields.Text(string='Comment')
    rejection_reason = fields.Text(string='Rejection Reason')
    signature = fields.Image(string='Signature', attachment=True, max_width=1024, max_height=1024)
    can_decide = fields.Boolean(compute='_compute_can_decide')

    @api.depends_context('uid')
    @api.depends('state', 'approver_user_ids')
    def _compute_can_decide(self):
        for line in self:
            line.can_decide = line.state == 'pending' and line._user_can_decide(self.env.user)

    def _user_can_decide(self, user):
        self.ensure_one()
        return user in self.approver_user_ids or user.has_group(
            'stock_picking_approval_advanced.group_picking_approval_manager')
