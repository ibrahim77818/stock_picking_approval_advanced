# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class StockPickingApprovalRule(models.Model):
    """Configuration engine: decides *whether* a transfer needs approval and
    *who* has to approve it, tier after tier."""

    _name = 'stock.picking.approval.rule'
    _description = 'Transfer Approval Rule'
    _inherit = ['mail.thread']
    _order = 'sequence, id'
    _check_company_auto = True

    name = fields.Char(required=True, translate=True, tracking=True)
    sequence = fields.Integer(
        default=10,
        help="Rules are evaluated in this order; the first matching rule applies.")
    active = fields.Boolean(default=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company)
    note = fields.Text(string='Description', translate=True)

    # ------------------------------------------------------------------ scope
    apply_on_incoming = fields.Boolean(string='Receipts', default=True, tracking=True)
    apply_on_outgoing = fields.Boolean(string='Delivery Orders', default=True, tracking=True)
    apply_on_internal = fields.Boolean(string='Internal Transfers', default=True, tracking=True)
    picking_type_ids = fields.Many2many(
        'stock.picking.type', 'stock_picking_approval_rule_picking_type_rel',
        'rule_id', 'picking_type_id', string='Operation Types', check_company=True,
        domain="[('code', 'in', ('incoming', 'outgoing', 'internal'))]",
        help="Restrict the rule to these operation types. Leave empty to apply "
             "to every operation type of the selected kinds.")
    warehouse_ids = fields.Many2many(
        'stock.warehouse', 'stock_picking_approval_rule_warehouse_rel',
        'rule_id', 'warehouse_id', string='Warehouses', check_company=True,
        help="Restrict the rule to transfers of these warehouses. Leave empty for all warehouses.")
    requester_user_ids = fields.Many2many(
        'res.users', 'stock_picking_approval_rule_requester_user_rel',
        'rule_id', 'user_id', string='Requesting Users',
        domain="[('share', '=', False)]",
        help="The rule only applies when one of these users validates the transfer. "
             "Leave users and groups empty to apply to everyone.")
    requester_group_ids = fields.Many2many(
        'res.groups', 'stock_picking_approval_rule_requester_group_rel',
        'rule_id', 'group_id', string='Requesting Groups',
        help="The rule only applies when a member of one of these groups validates the transfer.")

    # ------------------------------------------------------------------ tiers
    tier_ids = fields.One2many(
        'stock.picking.approval.tier', 'rule_id', string='Approval Tiers', copy=True)
    tier_count = fields.Integer(compute='_compute_tier_count', string='Tiers')

    # ---------------------------------------------------------------- options
    allow_self_approval = fields.Boolean(
        string='Allow Self-Approval', tracking=True,
        help="If unchecked, the user who requested the approval can never sign "
             "a tier of that request (separation of duties).")
    require_signature = fields.Boolean(
        string='Require Drawn Signature', tracking=True,
        help="Approvers must draw a signature to sign off their tier.")
    validate_on_approval = fields.Boolean(
        string='Validate on Final Approval', default=True, tracking=True,
        help="If checked, the transfer is validated automatically when the last tier "
             "approves. Otherwise it returns to Ready and must be validated manually.")
    notify_by_email = fields.Boolean(string='Email Notifications', default=True)
    schedule_activity = fields.Boolean(string='Schedule To-Do Activities', default=True)

    picking_count = fields.Integer(compute='_compute_picking_count', string='Transfers')

    @api.depends('tier_ids')
    def _compute_tier_count(self):
        for rule in self:
            rule.tier_count = len(rule.tier_ids)

    def _compute_picking_count(self):
        data = self.env['stock.picking']._read_group(
            [('approval_rule_id', 'in', self.ids)], ['approval_rule_id'], ['__count'])
        counts = {rule.id: count for rule, count in data}
        for rule in self:
            rule.picking_count = counts.get(rule.id, 0)

    @api.constrains('name', 'tier_ids', 'active')
    def _check_tiers(self):
        for rule in self:
            if rule.active and not rule.tier_ids:
                raise ValidationError(_(
                    "The approval rule \"%s\" must define at least one approval tier.", rule.name))

    @api.constrains('apply_on_incoming', 'apply_on_outgoing', 'apply_on_internal')
    def _check_operation_kinds(self):
        for rule in self:
            if not (rule.apply_on_incoming or rule.apply_on_outgoing or rule.apply_on_internal):
                raise ValidationError(_(
                    "The approval rule \"%s\" must apply to at least one kind of operation.", rule.name))

    # ---------------------------------------------------------------- engine
    def _get_applicable_codes(self):
        self.ensure_one()
        codes = []
        if self.apply_on_incoming:
            codes.append('incoming')
        if self.apply_on_outgoing:
            codes.append('outgoing')
        if self.apply_on_internal:
            codes.append('internal')
        return codes

    def _is_applicable(self, picking, user):
        """Return True if this rule governs ``picking`` validated by ``user``."""
        self.ensure_one()
        picking_type = picking.picking_type_id
        if self.company_id != picking.company_id:
            return False
        if picking_type.code not in self._get_applicable_codes():
            return False
        if self.picking_type_ids and picking_type not in self.picking_type_ids:
            return False
        if self.warehouse_ids and picking_type.warehouse_id not in self.warehouse_ids:
            return False
        if self.requester_user_ids or self.requester_group_ids:
            if user not in self.requester_user_ids and not (self.requester_group_ids & user.groups_id):
                return False
        return True

    @api.model
    def _find_rule(self, picking, user=None):
        """First active rule (by sequence) applicable to ``picking``."""
        user = user or self.env.user
        rules = self.sudo().search([('company_id', '=', picking.company_id.id)], order='sequence, id')
        for rule in rules:
            if rule._is_applicable(picking, user):
                return rule.sudo(False)
        return self.browse()

    def action_view_pickings(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('stock.action_picking_tree_all')
        action['domain'] = [('approval_rule_id', '=', self.id)]
        action['context'] = {'create': False}
        return action


class StockPickingApprovalTier(models.Model):
    _name = 'stock.picking.approval.tier'
    _description = 'Transfer Approval Tier'
    _order = 'sequence, id'

    rule_id = fields.Many2one(
        'stock.picking.approval.rule', string='Rule', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='rule_id.company_id', store=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(string='Tier', required=True, translate=True)
    approver_user_ids = fields.Many2many(
        'res.users', 'stock_picking_approval_tier_user_rel', 'tier_id', 'user_id',
        string='Approver Users', domain="[('share', '=', False)]")
    approver_group_ids = fields.Many2many(
        'res.groups', 'stock_picking_approval_tier_group_rel', 'tier_id', 'group_id',
        string='Approver Groups',
        help="Every internal user belonging to these groups (directly or through "
             "implied groups) can sign off this tier.")

    @api.constrains('name', 'approver_user_ids', 'approver_group_ids')
    def _check_approvers(self):
        for tier in self:
            if not tier.approver_user_ids and not tier.approver_group_ids:
                raise ValidationError(_(
                    "The approval tier \"%s\" needs at least one approver user or group.", tier.name))

    def _get_approvers(self, company):
        """Internal, active users allowed to sign this tier in ``company``."""
        self.ensure_one()
        # Odoo 18 stores implied memberships in res.groups.users, so this covers implied groups.
        users = (self.approver_user_ids | self.sudo().approver_group_ids.users).sudo()
        return users.filtered(
            lambda u: u.active and not u.share and company in u.company_ids
        ).sudo(False)
