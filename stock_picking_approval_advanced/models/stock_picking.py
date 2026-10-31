# -*- coding: utf-8 -*-
from markupsafe import Markup

from odoo import api, fields, models, _, Command
from odoo.exceptions import AccessError, UserError

APPROVAL_STATES = [
    ('none', 'Not Requested'),
    ('pending', 'Pending Approval'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
]

# Picking fields that must not change while a transfer is under review.
LOCKED_PICKING_FIELDS = {
    'move_ids', 'move_ids_without_package', 'move_line_ids', 'move_line_ids_without_package',
    'location_id', 'location_dest_id', 'picking_type_id', 'partner_id', 'owner_id',
    'company_id', 'move_type',
}
# Approval bookkeeping fields: only the approval engine may write them.
PROTECTED_APPROVAL_FIELDS = {
    'approval_rule_id', 'approval_state', 'approval_line_ids', 'approval_cycle',
    'approval_requested_by_id', 'approval_requested_date', 'approval_done_date',
    'approval_rejection_reason',
}


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    state = fields.Selection(
        selection_add=[('to_approve', 'Waiting for Approval'), ('done',)],
        ondelete={'to_approve': lambda recs: recs.write({'state': 'assigned'})})

    approval_rule_id = fields.Many2one(
        'stock.picking.approval.rule', string='Approval Rule', copy=False, readonly=True,
        ondelete='restrict', index='btree_not_null')
    approval_state = fields.Selection(
        APPROVAL_STATES, string='Approval Status', default='none', required=True,
        copy=False, readonly=True, index=True, tracking=True)
    approval_cycle = fields.Integer(string='Approval Cycle', copy=False, readonly=True)
    approval_line_ids = fields.One2many(
        'stock.picking.approval.line', 'picking_id', string='Approval History',
        copy=False, readonly=True)
    approval_requested_by_id = fields.Many2one(
        'res.users', string='Approval Requested By', copy=False, readonly=True)
    approval_requested_date = fields.Datetime(string='Approval Requested On', copy=False, readonly=True)
    approval_done_date = fields.Datetime(string='Approved On', copy=False, readonly=True)
    approval_rejection_reason = fields.Text(string='Last Rejection Reason', copy=False, readonly=True)

    approval_current_line_id = fields.Many2one(
        'stock.picking.approval.line', string='Current Tier',
        compute='_compute_approval_current')
    approval_current_approver_ids = fields.Many2many(
        'res.users', string='Current Approvers', compute='_compute_approval_current')
    approval_progress = fields.Char(string='Approval Progress', compute='_compute_approval_current')
    approval_can_decide = fields.Boolean(
        string='Can Approve', compute='_compute_approval_can_decide')
    approval_can_recall = fields.Boolean(
        string='Can Recall', compute='_compute_approval_can_decide')
    approval_is_mine = fields.Boolean(
        string='Awaiting My Approval', compute='_compute_approval_can_decide',
        search='_search_approval_is_mine')
    approval_mail_partner_to = fields.Char(compute='_compute_approval_mail_info')
    approval_record_url = fields.Char(compute='_compute_approval_mail_info')

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends('approval_state')
    def _compute_state(self):
        super()._compute_state()
        for picking in self:
            if picking.approval_state == 'pending' and picking.state not in ('draft', 'cancel', 'done'):
                picking.state = 'to_approve'

    @api.depends('approval_cycle', 'approval_line_ids.state', 'approval_line_ids.cycle',
                 'approval_line_ids.approver_user_ids')
    def _compute_approval_current(self):
        for picking in self:
            lines = picking._approval_cycle_lines()
            current = lines.filtered(lambda l: l.state == 'pending')[:1]
            picking.approval_current_line_id = current
            picking.approval_current_approver_ids = current.approver_user_ids
            if lines:
                done = len(lines.filtered(lambda l: l.state == 'approved'))
                picking.approval_progress = '%s / %s' % (done, len(lines))
            else:
                picking.approval_progress = False

    @api.depends('approval_current_approver_ids')
    def _compute_approval_mail_info(self):
        for picking in self:
            partners = picking.approval_current_approver_ids.partner_id
            picking.approval_mail_partner_to = ','.join(str(pid) for pid in partners.ids)
            picking.approval_record_url = '%s/odoo/stock.picking/%s' % (picking.get_base_url(), picking.id)

    @api.depends_context('uid')
    @api.depends('state', 'approval_state', 'approval_requested_by_id',
                 'approval_line_ids.state', 'approval_line_ids.approver_user_ids')
    def _compute_approval_can_decide(self):
        user = self.env.user
        is_manager = user.has_group('stock_picking_approval_advanced.group_picking_approval_manager')
        for picking in self:
            line = picking.approval_current_line_id
            pending = picking.state == 'to_approve' and bool(line)
            picking.approval_is_mine = pending and user in line.approver_user_ids
            picking.approval_can_decide = pending and line._user_can_decide(user) and (
                picking.approval_rule_id.allow_self_approval
                or user != picking.approval_requested_by_id)
            picking.approval_can_recall = picking.state == 'to_approve' and (
                is_manager or user == picking.approval_requested_by_id)

    def _search_approval_is_mine(self, operator, value):
        if operator not in ('in', 'not in'):
            return NotImplemented
        domain = [
            ('state', '=', 'to_approve'),
            ('approval_line_ids', 'any', [
                ('state', '=', 'pending'),
                ('approver_user_ids', 'in', [self.env.uid]),
            ]),
        ]
        positive = (operator == 'in') == (True in value)
        return domain if positive else ['!', '&', *domain]

    # ------------------------------------------------------------------
    # ORM guards
    # ------------------------------------------------------------------
    def write(self, vals):
        if not self.env.context.get('picking_approval_internal') and not self.env.su:
            if PROTECTED_APPROVAL_FIELDS & set(vals):
                raise AccessError(_(
                    "Approval information is managed by the approval workflow and cannot be edited manually."))
            if LOCKED_PICKING_FIELDS & set(vals):
                self._approval_check_not_locked()
        return super().write(vals)

    def _approval_check_not_locked(self):
        """Raise if one of the transfers is under review (operations are frozen)."""
        if self.env.context.get('picking_approval_bypass'):
            return
        locked = self.filtered(lambda p: p.state == 'to_approve')
        if locked:
            raise UserError(_(
                "The following transfers are waiting for approval; their operations and "
                "reservations are locked until the request is approved, rejected or recalled:\n%s",
                "\n".join(locked.mapped('display_name'))))

    def _approval_reset_after_change(self):
        """An approved transfer whose operations change loses its approval."""
        if self.env.context.get('picking_approval_bypass'):
            return
        changed = self.filtered(lambda p: p.approval_state == 'approved' and p.state not in ('done', 'cancel'))
        if changed:
            changed._approval_write({'approval_state': 'none', 'approval_done_date': False})
            for picking in changed:
                picking.message_post(body=_(
                    "The operations were modified after approval: the approval has been "
                    "withdrawn and the transfer must be approved again."))

    # ------------------------------------------------------------------
    # Interception of the standard flow
    # ------------------------------------------------------------------
    def button_validate(self):
        if self.env.context.get('skip_picking_approval'):
            return super().button_validate()

        pickings = self.filtered(lambda p: p.state != 'done')
        waiting = pickings.filtered(lambda p: p.state == 'to_approve')
        if waiting:
            raise UserError(_(
                "The following transfers are already waiting for approval and cannot be "
                "validated yet:\n%s", "\n".join(waiting.mapped('display_name'))))

        rule_by_picking = {}
        for picking in pickings.filtered(lambda p: p.approval_state != 'approved'):
            rule = self.env['stock.picking.approval.rule']._find_rule(picking)
            if rule:
                rule_by_picking[picking.id] = rule
        to_submit = self.browse(list(rule_by_picking))
        direct = self - to_submit

        result = True
        if direct:
            result = super(StockPicking, direct.with_context(picking_approval_bypass=True)).button_validate()
        if to_submit:
            to_submit._approval_prepare_submission()
            for picking in to_submit:
                picking._approval_submit(rule_by_picking[picking.id])
            if not direct:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'type': 'info',
                        'title': _("Approval requested"),
                        'message': _("%s submitted for approval. The approvers have been notified.",
                                     ", ".join(to_submit.mapped('name'))),
                        'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
                    },
                }
        return result

    def _action_done(self):
        if not self.env.context.get('picking_approval_bypass'):
            blocked = self.filtered(lambda p: p.approval_state in ('pending', 'rejected'))
            if blocked:
                raise UserError(_(
                    "The following transfers cannot be processed because their approval is "
                    "pending or was rejected:\n%s", "\n".join(blocked.mapped('display_name'))))
        return super()._action_done()

    def do_unreserve(self):
        self._approval_check_not_locked()
        return super().do_unreserve()

    def action_assign(self):
        self._approval_check_not_locked()
        return super().action_assign()

    def action_cancel(self):
        pending = self.filtered(lambda p: p.state == 'to_approve')
        if pending:
            pending._approval_close_cycle('cancelled')
            pending._approval_write({'approval_state': 'none'})
            for picking in pending:
                picking.message_post(body=_("Approval request cancelled with the transfer."))
        return super(StockPicking, self.with_context(picking_approval_bypass=True)).action_cancel()

    # ------------------------------------------------------------------
    # Approval engine
    # ------------------------------------------------------------------
    def _approval_write(self, vals):
        return self.with_context(picking_approval_internal=True, picking_approval_bypass=True).write(vals)

    def _approval_cycle_lines(self):
        self.ensure_one()
        return self.approval_line_ids.filtered(
            lambda l: l.cycle == self.approval_cycle).sorted(lambda l: (l.sequence, l.id))

    def _approval_prepare_submission(self):
        """Same preparation as the standard validation, without executing it."""
        draft = self.filtered(lambda p: p.state == 'draft')
        draft.action_confirm()
        for move in draft.move_ids:
            if move.product_uom.is_zero(move.quantity) and not move.product_uom.is_zero(move.product_uom_qty):
                move.quantity = move.product_uom_qty
        if not self.env.context.get('skip_sanity_check'):
            self._sanity_check()
        if any(all(m.product_uom.is_zero(m.quantity) for m in p.move_ids) for p in self):
            raise UserError(_("You cannot request approval for a transfer without any quantity to process."))

    def _approval_submit(self, rule):
        self.ensure_one()
        requester = self.env.user
        cycle = self.approval_cycle + 1
        line_commands = []
        for index, tier in enumerate(rule.tier_ids.sorted(lambda t: (t.sequence, t.id))):
            approvers = tier._get_approvers(self.company_id)
            if not rule.allow_self_approval:
                approvers -= requester
            if not approvers:
                raise UserError(_(
                    "No eligible approver was found for tier \"%(tier)s\" of the approval rule "
                    "\"%(rule)s\". Please contact your approval administrator.",
                    tier=tier.name, rule=rule.name))
            line_commands.append(Command.create({
                'rule_id': rule.id,
                'tier_id': tier.id,
                'cycle': cycle,
                'sequence': index + 1,
                'name': tier.name,
                'approver_user_ids': [Command.set(approvers.ids)],
                'state': 'pending' if index == 0 else 'waiting',
            }))
        self.sudo()._approval_write({
            'approval_rule_id': rule.id,
            'approval_cycle': cycle,
            'approval_state': 'pending',
            'approval_requested_by_id': requester.id,
            'approval_requested_date': fields.Datetime.now(),
            'approval_done_date': False,
            'approval_rejection_reason': False,
            'approval_line_ids': line_commands,
        })
        self.message_post(body=Markup(_(
            "Approval requested under rule <b>%(rule)s</b> (%(count)s tier(s)).")) % {
                'rule': rule.name, 'count': len(line_commands)})
        self._approval_notify_pending(self._approval_cycle_lines()[:1])

    def _approval_check_can_decide(self):
        self.ensure_one()
        line = self.approval_current_line_id
        if self.state != 'to_approve' or not line:
            raise UserError(_("The transfer %s is not waiting for approval.", self.display_name))
        user = self.env.user
        if not line._user_can_decide(user):
            raise AccessError(_(
                "You are not an authorized approver for tier \"%(tier)s\" of %(picking)s.",
                tier=line.name, picking=self.display_name))
        if not self.approval_rule_id.allow_self_approval and user == self.approval_requested_by_id:
            raise AccessError(_(
                "Separation of duties: you cannot approve or reject a request you submitted yourself."))
        return line

    def _approval_do_approve(self, comment=False, signature=False):
        """Sign the current tier. Returns the validation action on final approval."""
        self.ensure_one()
        line = self._approval_check_can_decide()
        rule = self.approval_rule_id
        if rule.require_signature and not signature:
            raise UserError(_("A drawn signature is required to approve this tier."))
        line.sudo().write({
            'state': 'approved',
            'user_id': self.env.uid,
            'date': fields.Datetime.now(),
            'comment': comment or False,
            'signature': signature or False,
        })
        self._approval_clear_activities(line, done=True)
        body = Markup(_("Tier <b>%s</b> approved.")) % line.name
        if comment:
            body += Markup('<br/>') + comment
        self.message_post(body=body)

        next_line = self._approval_cycle_lines().filtered(lambda l: l.state == 'waiting')[:1]
        if next_line:
            next_line.sudo().state = 'pending'
            self._approval_notify_pending(next_line)
            return False

        self._approval_write({'approval_state': 'approved', 'approval_done_date': fields.Datetime.now()})
        self.message_post(body=_("All approval tiers signed: the transfer is approved."))
        self._approval_send_template('stock_picking_approval_advanced.mail_template_picking_approval_approved')
        if rule.validate_on_approval:
            return self.with_context(picking_approval_bypass=True).button_validate()
        return False

    def _approval_do_reject(self, reason):
        self.ensure_one()
        if not reason or not reason.strip():
            raise UserError(_("A rejection reason is mandatory."))
        line = self._approval_check_can_decide()
        line.sudo().write({
            'state': 'rejected',
            'user_id': self.env.uid,
            'date': fields.Datetime.now(),
            'rejection_reason': reason,
        })
        self._approval_clear_activities(line, done=False)
        self._approval_close_cycle('cancelled')
        self._approval_write({'approval_state': 'rejected', 'approval_rejection_reason': reason})
        self.message_post(body=Markup(_("Tier <b>%(tier)s</b> rejected. Reason: %(reason)s")) % {
            'tier': line.name, 'reason': reason})
        self._approval_send_template('stock_picking_approval_advanced.mail_template_picking_approval_rejected')
        return False

    def _approval_close_cycle(self, state):
        """Close every open line of the current cycle and remove their activities."""
        for picking in self:
            open_lines = picking._approval_cycle_lines().filtered(lambda l: l.state in ('waiting', 'pending'))
            for line in open_lines.filtered(lambda l: l.state == 'pending'):
                picking._approval_clear_activities(line, done=False)
            open_lines.sudo().write({'state': state})

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------
    def _approval_notify_pending(self, line):
        self.ensure_one()
        if not line:
            return
        rule = self.approval_rule_id
        if rule.schedule_activity:
            # quick_update: no extra "activity assigned" email, the template covers it
            for user in line.approver_user_ids:
                self.sudo().with_context(mail_activity_quick_update=True).activity_schedule(
                    'stock_picking_approval_advanced.mail_activity_type_picking_approval',
                    user_id=user.id,
                    summary=_("Approve %(picking)s (%(tier)s)", picking=self.name, tier=line.name),
                    note=_("Transfer %s is waiting for your approval.", self.name),
                )
        if rule.notify_by_email:
            self._approval_send_template(
                'stock_picking_approval_advanced.mail_template_picking_approval_request')

    def _approval_send_template(self, xmlid):
        self.ensure_one()
        rule = self.approval_rule_id
        if not rule.notify_by_email:
            return
        template = self.env.ref(xmlid, raise_if_not_found=False)
        if template:
            template.sudo().send_mail(self.id, force_send=False)

    def _approval_clear_activities(self, line, done):
        self.ensure_one()
        activities = self.sudo().activity_ids.filtered(
            lambda a: a.activity_type_id == self.env.ref(
                'stock_picking_approval_advanced.mail_activity_type_picking_approval',
                raise_if_not_found=False))
        if not activities:
            return
        mine = activities.filtered(lambda a: a.user_id == self.env.user) if done else activities.browse()
        if mine:
            mine.action_feedback(feedback=_("Tier %s approved.", line.name))
        (activities - mine).unlink()

    # ------------------------------------------------------------------
    # UI actions
    # ------------------------------------------------------------------
    def _approval_open_wizard(self, action):
        self.ensure_one()
        self._approval_check_can_decide()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Approve Transfer") if action == 'approve' else _("Reject Transfer"),
            'res_model': 'stock.picking.approval.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_picking_id': self.id,
                'default_action': action,
            },
        }

    def action_approval_approve(self):
        return self._approval_open_wizard('approve')

    def action_approval_reject(self):
        return self._approval_open_wizard('reject')

    def action_approval_recall(self):
        for picking in self:
            if not picking.approval_can_recall:
                raise AccessError(_(
                    "Only the requester or an approval administrator can recall the approval request of %s.",
                    picking.display_name))
        self._approval_close_cycle('cancelled')
        self._approval_write({'approval_state': 'none'})
        for picking in self:
            picking.message_post(body=_("Approval request recalled by %s.", self.env.user.name))
        return True
