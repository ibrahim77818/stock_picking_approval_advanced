# -*- coding: utf-8 -*-
from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install', 'stock_picking_approval')
class TestPickingApproval(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mail_notrack=True))
        cls.company = cls.env.company
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        cls.stock_location = cls.warehouse.lot_stock_id
        cls.shelf = cls.env['stock.location'].create({
            'name': 'Approval Shelf', 'location_id': cls.stock_location.id, 'usage': 'internal',
        })
        cls.customer_location = cls.env.ref('stock.stock_location_customers')
        cls.supplier_location = cls.env.ref('stock.stock_location_suppliers')
        cls.type_in = cls.warehouse.in_type_id
        cls.type_out = cls.warehouse.out_type_id
        cls.type_int = cls.warehouse.int_type_id
        cls.type_int.active = True

        cls.product = cls.env['product.product'].create({
            'name': 'Approval Widget', 'type': 'consu', 'is_storable': True,
        })
        cls.env['stock.quant']._update_available_quantity(cls.product, cls.stock_location, 100)

        stock_user = 'stock.group_stock_user'
        approver = 'stock_picking_approval_advanced.group_picking_approval_user'
        manager = 'stock_picking_approval_advanced.group_picking_approval_manager'
        cls.requester = new_test_user(cls.env, login='spa_requester', groups=stock_user,
                                      email='requester@example.com')
        cls.approver_1 = new_test_user(cls.env, login='spa_approver1', groups=approver,
                                       email='approver1@example.com')
        cls.approver_2 = new_test_user(cls.env, login='spa_approver2', groups=approver,
                                       email='approver2@example.com')
        cls.outsider = new_test_user(cls.env, login='spa_outsider', groups=approver,
                                     email='outsider@example.com')
        cls.admin_approver = new_test_user(cls.env, login='spa_admin', groups=manager,
                                           email='spa.admin@example.com')

        # Tier 2 is group based: only members of "Warehouse Supervisors" can sign it.
        cls.supervisors = cls.env['res.groups'].create({
            'name': 'Warehouse Supervisors (test)',
            'users': [Command.link(cls.approver_2.id)],
        })

        cls.rule = cls.env['stock.picking.approval.rule'].create({
            'name': 'Outbound & internal control',
            'sequence': 1,
            'apply_on_incoming': False,
            'apply_on_outgoing': True,
            'apply_on_internal': True,
            'warehouse_ids': [Command.set(cls.warehouse.ids)],
            'tier_ids': [
                Command.create({'name': 'Team Leader', 'sequence': 1,
                                'approver_user_ids': [Command.set(cls.approver_1.ids)]}),
                Command.create({'name': 'Supervisor', 'sequence': 2,
                                'approver_group_ids': [Command.set(cls.supervisors.ids)]}),
            ],
        })

    # ------------------------------------------------------------------ helpers
    def _create_picking(self, picking_type, src, dest, qty=10, confirm=True):
        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'location_id': src.id,
            'location_dest_id': dest.id,
            'move_ids': [Command.create({
                'name': self.product.display_name,
                'product_id': self.product.id,
                'product_uom_qty': qty,
                'location_id': src.id,
                'location_dest_id': dest.id,
            })],
        })
        if confirm:
            picking.action_confirm()
            picking.action_assign()
        return picking

    def _delivery(self, qty=10):
        return self._create_picking(self.type_out, self.stock_location, self.customer_location, qty)

    def _submit(self, picking, user=None):
        return picking.with_user(user or self.requester).button_validate()

    def _decide(self, picking, user, action='approve', **values):
        wizard = self.env['stock.picking.approval.wizard'].with_user(user).create(
            dict({'picking_id': picking.id, 'action': action}, **values))
        return wizard.action_confirm()

    def _on_hand(self):
        return sum(self.env['stock.quant'].search([
            ('product_id', '=', self.product.id),
            ('location_id', 'child_of', self.stock_location.id),
        ]).mapped('quantity'))

    # ------------------------------------------------------- state interception
    def test_01_no_rule_validates_directly(self):
        """Receipts are not covered by the rule: standard flow untouched."""
        receipt = self._create_picking(self.type_in, self.supplier_location, self.stock_location)
        self._submit(receipt)
        self.assertEqual(receipt.state, 'done')
        self.assertEqual(receipt.approval_state, 'none')
        self.assertFalse(receipt.approval_line_ids)

    def test_02_validation_is_intercepted(self):
        delivery = self._delivery()
        self.assertEqual(delivery.state, 'assigned')
        result = self._submit(delivery)

        self.assertEqual(result['tag'], 'display_notification')
        self.assertEqual(delivery.state, 'to_approve')
        self.assertEqual(delivery.approval_state, 'pending')
        self.assertEqual(delivery.approval_rule_id, self.rule)
        self.assertEqual(delivery.approval_requested_by_id, self.requester)
        self.assertEqual(delivery.approval_cycle, 1)
        self.assertEqual(delivery.approval_line_ids.mapped('state'), ['pending', 'waiting'])
        self.assertEqual(delivery.approval_current_line_id.name, 'Team Leader')
        self.assertEqual(delivery.approval_current_approver_ids, self.approver_1)
        self.assertEqual(delivery.approval_progress, '0 / 2')
        # Inventory is untouched and the reservation is kept.
        self.assertEqual(self._on_hand(), 100)
        self.assertEqual(delivery.move_ids.state, 'assigned')
        self.assertEqual(delivery.move_ids.quantity, 10)

    def test_03_draft_transfer_is_confirmed_on_submission(self):
        delivery = self._create_picking(self.type_out, self.stock_location, self.customer_location, confirm=False)
        self.assertEqual(delivery.state, 'draft')
        self._submit(delivery)
        self.assertEqual(delivery.state, 'to_approve')
        self.assertEqual(delivery.move_ids.quantity, 10)

    def test_04_cannot_validate_while_pending(self):
        delivery = self._delivery()
        self._submit(delivery)
        with self.assertRaises(UserError):
            delivery.with_user(self.requester).button_validate()
        with self.assertRaises(UserError):
            delivery.with_user(self.admin_approver).button_validate()

    def test_05_operations_and_reservation_locked(self):
        delivery = self._delivery()
        self._submit(delivery)
        with self.assertRaises(UserError):
            delivery.move_ids.with_user(self.requester).write({'quantity': 3})
        with self.assertRaises(UserError):
            delivery.move_ids.move_line_ids.with_user(self.requester).write({'quantity': 1})
        with self.assertRaises(UserError):
            delivery.with_user(self.requester).do_unreserve()
        with self.assertRaises(UserError):
            delivery.with_user(self.requester).write({'location_dest_id': self.shelf.id})
        with self.assertRaises(UserError):
            delivery.with_user(self.requester).write({'move_ids': [Command.create({
                'name': self.product.display_name, 'product_id': self.product.id, 'product_uom_qty': 1,
                'location_id': self.stock_location.id, 'location_dest_id': self.customer_location.id,
            })]})
        self.assertEqual(delivery.move_ids.quantity, 10)
        self.assertEqual(delivery.move_ids.state, 'assigned')

    def test_06_action_done_guard(self):
        """Even a direct call to the engine cannot execute a pending transfer."""
        delivery = self._delivery()
        self._submit(delivery)
        with self.assertRaises(UserError):
            delivery._action_done()
        self.assertEqual(delivery.state, 'to_approve')

    def test_07_sequential_full_approval(self):
        delivery = self._delivery()
        self._submit(delivery)

        # Tier 2 approver cannot jump ahead of tier 1.
        with self.assertRaises(AccessError):
            self._decide(delivery, self.approver_2)

        self._decide(delivery, self.approver_1, comment='Checked stock levels')
        line_1, line_2 = delivery.approval_line_ids.sorted('sequence')
        self.assertEqual(line_1.state, 'approved')
        self.assertEqual(line_1.user_id, self.approver_1)
        self.assertEqual(line_1.comment, 'Checked stock levels')
        self.assertTrue(line_1.date)
        self.assertEqual(line_2.state, 'pending')
        self.assertEqual(line_2.approver_user_ids, self.approver_2, "Group-based tier resolved to members")
        self.assertEqual(delivery.state, 'to_approve')

        # Tier 1 approver cannot sign tier 2.
        with self.assertRaises(AccessError):
            self._decide(delivery, self.approver_1)

        self._decide(delivery, self.approver_2)
        self.assertEqual(line_2.state, 'approved')
        self.assertEqual(delivery.approval_state, 'approved')
        self.assertTrue(delivery.approval_done_date)
        self.assertEqual(delivery.state, 'done', "Validated automatically on final approval")
        self.assertEqual(self._on_hand(), 90)

    def test_08_unauthorized_user_rejected(self):
        delivery = self._delivery()
        self._submit(delivery)
        self.assertFalse(delivery.with_user(self.outsider).approval_can_decide)
        with self.assertRaises(AccessError):
            self._decide(delivery, self.outsider)
        with self.assertRaises(AccessError):
            self._decide(delivery, self.outsider, action='reject', rejection_reason='No')
        with self.assertRaises(AccessError):
            delivery.with_user(self.outsider).action_approval_approve()
        self.assertEqual(delivery.approval_line_ids.mapped('state'), ['pending', 'waiting'])

    def test_09_manager_override(self):
        delivery = self._delivery()
        self._submit(delivery)
        self.assertTrue(delivery.with_user(self.admin_approver).approval_can_decide)
        self._decide(delivery, self.admin_approver)
        self._decide(delivery, self.admin_approver)
        self.assertEqual(delivery.state, 'done')
        self.assertEqual(set(delivery.approval_line_ids.mapped('user_id').ids), {self.admin_approver.id})

    def test_10_self_approval_blocked(self):
        delivery = self._delivery()
        self._submit(delivery, user=self.admin_approver)
        self.assertFalse(delivery.with_user(self.admin_approver).approval_can_decide)
        with self.assertRaises(AccessError):
            self._decide(delivery, self.admin_approver)

        # Requester is the only approver of a tier: no eligible approver left.
        self.rule.tier_ids[0].approver_user_ids = self.requester
        other = self._delivery()
        with self.assertRaisesRegex(UserError, 'No eligible approver'):
            self._submit(other)
        self.assertEqual(other.state, 'assigned')

        # Allowing self-approval makes the requester eligible again.
        self.rule.allow_self_approval = True
        self._submit(other)
        self._decide(other, self.requester)
        self.assertEqual(other.approval_line_ids.sorted('sequence')[0].state, 'approved')

    def test_11_rejection_requires_reason(self):
        delivery = self._delivery()
        self._submit(delivery)
        with self.assertRaises(UserError):
            self._decide(delivery, self.approver_1, action='reject', rejection_reason='   ')
        with self.assertRaises(UserError):
            delivery.with_user(self.approver_1)._approval_do_reject('')
        self.assertEqual(delivery.state, 'to_approve')

    def test_12_rejection_and_new_cycle(self):
        delivery = self._delivery()
        self._submit(delivery)
        self._decide(delivery, self.approver_1, action='reject', rejection_reason='Wrong customer')

        self.assertEqual(delivery.approval_state, 'rejected')
        self.assertEqual(delivery.approval_rejection_reason, 'Wrong customer')
        self.assertEqual(delivery.state, 'assigned', "Back to Ready with reservation kept")
        self.assertEqual(delivery.move_ids.quantity, 10)
        line_1, line_2 = delivery.approval_line_ids.sorted('sequence')
        self.assertEqual((line_1.state, line_1.rejection_reason, line_1.user_id),
                         ('rejected', 'Wrong customer', self.approver_1))
        self.assertEqual(line_2.state, 'cancelled')

        # A rejected transfer cannot be executed behind the workflow's back.
        with self.assertRaises(UserError):
            delivery._action_done()

        # Operations are editable again, and validating restarts the workflow.
        delivery.move_ids.with_user(self.requester).write({'quantity': 8})
        self._submit(delivery)
        self.assertEqual(delivery.approval_cycle, 2)
        self.assertEqual(delivery.approval_state, 'pending')
        self.assertEqual(len(delivery.approval_line_ids), 4, "History of cycle 1 is kept")
        self.assertEqual(delivery._approval_cycle_lines().mapped('state'), ['pending', 'waiting'])

    def test_13_recall(self):
        delivery = self._delivery()
        self._submit(delivery)
        with self.assertRaises(AccessError):
            delivery.with_user(self.outsider).action_approval_recall()
        delivery.with_user(self.requester).action_approval_recall()
        self.assertEqual(delivery.approval_state, 'none')
        self.assertEqual(delivery.state, 'assigned')
        self.assertEqual(set(delivery.approval_line_ids.mapped('state')), {'cancelled'})

    def test_14_cancel_pending_transfer(self):
        delivery = self._delivery()
        self._submit(delivery)
        delivery.action_cancel()
        self.assertEqual(delivery.state, 'cancel')
        self.assertEqual(delivery.approval_state, 'none')
        self.assertEqual(set(delivery.approval_line_ids.mapped('state')), {'cancelled'})

    # -------------------------------------------------------------- rule scope
    def test_20_scope_operation_type_and_warehouse(self):
        internal = self._create_picking(self.type_int, self.stock_location, self.shelf)
        self._submit(internal)
        self.assertEqual(internal.state, 'to_approve')

        self.rule.picking_type_ids = self.type_out
        internal_2 = self._create_picking(self.type_int, self.stock_location, self.shelf)
        self._submit(internal_2)
        self.assertEqual(internal_2.state, 'done', "Operation type not targeted by the rule")

        other_wh = self.env['stock.warehouse'].create({'name': 'Other WH', 'code': 'OWH'})
        self.rule.picking_type_ids = False
        self.rule.warehouse_ids = other_wh
        delivery = self._delivery()
        self._submit(delivery)
        self.assertEqual(delivery.state, 'done', "Warehouse not targeted by the rule")

    def test_21_scope_requesting_users_and_groups(self):
        self.rule.requester_user_ids = self.outsider
        delivery = self._delivery()
        self._submit(delivery)
        self.assertEqual(delivery.state, 'done', "Requester not targeted by the rule")

        requester_group = self.env['res.groups'].create({
            'name': 'Junior Storekeepers (test)', 'users': [Command.link(self.requester.id)]})
        self.rule.requester_group_ids = requester_group
        delivery_2 = self._delivery()
        self._submit(delivery_2)
        self.assertEqual(delivery_2.state, 'to_approve', "Requester matched through group")

    def test_22_rule_priority(self):
        """The first matching rule by sequence wins."""
        express = self.env['stock.picking.approval.rule'].create({
            'name': 'Express', 'sequence': 0, 'apply_on_incoming': False, 'apply_on_internal': False,
            'tier_ids': [Command.create({'name': 'Single', 'approver_user_ids': [Command.set(self.approver_2.ids)]})],
        })
        delivery = self._delivery()
        self._submit(delivery)
        self.assertEqual(delivery.approval_rule_id, express)
        self.assertEqual(len(delivery.approval_line_ids), 1)

    def test_23_archived_rule_ignored(self):
        self.rule.active = False
        delivery = self._delivery()
        self._submit(delivery)
        self.assertEqual(delivery.state, 'done')

    # ------------------------------------------------------- options & guards
    def test_30_manual_validation_after_approval(self):
        self.rule.validate_on_approval = False
        delivery = self._delivery()
        self._submit(delivery)
        self._decide(delivery, self.approver_1)
        self._decide(delivery, self.approver_2)
        self.assertEqual(delivery.approval_state, 'approved')
        self.assertEqual(delivery.state, 'assigned')
        self._submit(delivery)
        self.assertEqual(delivery.state, 'done', "Approved transfer validates without a new cycle")
        self.assertEqual(delivery.approval_cycle, 1)

    def test_31_change_after_approval_withdraws_approval(self):
        self.rule.validate_on_approval = False
        delivery = self._delivery()
        self._submit(delivery)
        self._decide(delivery, self.approver_1)
        self._decide(delivery, self.approver_2)
        delivery.move_ids.with_user(self.requester).write({'quantity': 5})
        self.assertEqual(delivery.approval_state, 'none')
        self._submit(delivery)
        self.assertEqual(delivery.state, 'to_approve')
        self.assertEqual(delivery.approval_cycle, 2)

    def test_32_signature_required(self):
        self.rule.require_signature = True
        delivery = self._delivery()
        self._submit(delivery)
        with self.assertRaises(UserError):
            self._decide(delivery, self.approver_1)
        # 1x1 transparent PNG
        png = b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='
        self._decide(delivery, self.approver_1, signature=png)
        self.assertTrue(delivery.approval_line_ids.sorted('sequence')[0].signature)

    def test_33_approval_fields_protected(self):
        delivery = self._delivery()
        with self.assertRaises(AccessError):
            delivery.with_user(self.requester).write({'approval_state': 'approved'})
        self._submit(delivery)
        line = delivery.approval_current_line_id
        with self.assertRaises(AccessError):
            line.with_user(self.approver_1).write({'state': 'approved'})

    def test_34_backorder_needs_its_own_approval(self):
        self.type_out.create_backorder = 'always'
        delivery = self._delivery()
        delivery.move_ids.quantity = 6
        self._submit(delivery)
        self._decide(delivery, self.approver_1)
        self._decide(delivery, self.approver_2)
        self.assertEqual(delivery.state, 'done')
        backorder = self.env['stock.picking'].search([('backorder_id', '=', delivery.id)])
        self.assertEqual(backorder.move_ids.product_uom_qty, 4)
        self.assertEqual(backorder.approval_state, 'none')
        self.assertFalse(backorder.approval_line_ids)
        self._submit(backorder)
        self.assertEqual(backorder.state, 'to_approve')

    # --------------------------------------------------------- notifications
    def test_40_email_and_activity_notifications(self):
        delivery = self._delivery()
        self._submit(delivery)
        activity_type = self.env.ref('stock_picking_approval_advanced.mail_activity_type_picking_approval')

        mails = self.env['mail.mail'].sudo().search([('model', '=', 'stock.picking'), ('res_id', '=', delivery.id)])
        self.assertEqual(len(mails), 1)
        self.assertEqual(mails.recipient_ids, self.approver_1.partner_id)
        self.assertIn('Approval required', mails.subject)
        activities = delivery.activity_ids.filtered(lambda a: a.activity_type_id == activity_type)
        self.assertEqual(activities.user_id, self.approver_1)

        self._decide(delivery, self.approver_1)
        mails = self.env['mail.mail'].sudo().search(
            [('model', '=', 'stock.picking'), ('res_id', '=', delivery.id)], order='id desc')
        self.assertEqual(mails[0].recipient_ids, self.approver_2.partner_id)
        activities = delivery.activity_ids.filtered(lambda a: a.activity_type_id == activity_type)
        self.assertEqual(activities.user_id, self.approver_2, "Tier 1 activity closed, tier 2 scheduled")

        self._decide(delivery, self.approver_2, action='reject', rejection_reason='Damaged goods')
        mails = self.env['mail.mail'].sudo().search(
            [('model', '=', 'stock.picking'), ('res_id', '=', delivery.id)], order='id desc')
        self.assertEqual(mails[0].recipient_ids, self.requester.partner_id)
        self.assertIn('Rejected', mails[0].subject)
        self.assertFalse(delivery.activity_ids.filtered(lambda a: a.activity_type_id == activity_type))

    def test_41_notifications_disabled(self):
        self.rule.write({'notify_by_email': False, 'schedule_activity': False})
        delivery = self._delivery()
        self._submit(delivery)
        self.assertFalse(self.env['mail.mail'].sudo().search(
            [('model', '=', 'stock.picking'), ('res_id', '=', delivery.id)]))
        self.assertFalse(delivery.activity_ids)

    def test_42_my_approvals_search(self):
        delivery = self._delivery()
        self._submit(delivery)
        Picking = self.env['stock.picking']
        self.assertIn(delivery, Picking.with_user(self.approver_1).search([('approval_is_mine', '=', True)]))
        self.assertNotIn(delivery, Picking.with_user(self.approver_2).search([('approval_is_mine', '=', True)]))
        self.assertNotIn(delivery, Picking.with_user(self.approver_1).search([('approval_is_mine', '=', False)]))

    # ------------------------------------------------------------ configuration
    def test_50_rule_constraints(self):
        Rule = self.env['stock.picking.approval.rule']
        with self.assertRaises(ValidationError):
            Rule.create({'name': 'No tiers'})
        with self.assertRaises(ValidationError):
            Rule.create({'name': 'Empty tier', 'tier_ids': [Command.create({'name': 'Nobody'})]})
        with self.assertRaises(ValidationError):
            Rule.create({
                'name': 'No kinds', 'apply_on_incoming': False, 'apply_on_outgoing': False,
                'apply_on_internal': False,
                'tier_ids': [Command.create({'name': 'T', 'approver_user_ids': [Command.set(self.approver_1.ids)]})],
            })

    def test_51_rule_configuration_restricted_to_admins(self):
        with self.assertRaises(AccessError):
            self.env['stock.picking.approval.rule'].with_user(self.requester).create({
                'name': 'Sneaky', 'tier_ids': [Command.create({
                    'name': 'T', 'approver_user_ids': [Command.set(self.requester.ids)]})],
            })
        with self.assertRaises(AccessError):
            self.rule.with_user(self.approver_1).write({'active': False})
        self.rule.with_user(self.admin_approver).write({'name': 'Renamed by admin'})
        self.assertEqual(self.rule.name, 'Renamed by admin')
