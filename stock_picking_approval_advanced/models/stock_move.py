# -*- coding: utf-8 -*-
from odoo import api, models

LOCKED_MOVE_FIELDS = {
    'product_id', 'product_uom_qty', 'quantity', 'product_uom', 'picked',
    'location_id', 'location_dest_id', 'picking_id',
}
LOCKED_MOVE_LINE_FIELDS = {
    'product_id', 'quantity', 'product_uom_id', 'picked', 'lot_id', 'lot_name',
    'location_id', 'location_dest_id', 'package_id', 'result_package_id', 'owner_id',
    'move_id', 'picking_id',
}


def _guard(records, vals=None, locked_fields=None):
    """Freeze operations of transfers under review; withdraw approvals of
    transfers whose operations change after being approved."""
    if records.env.context.get('picking_approval_bypass'):
        return
    if vals is not None and not (locked_fields & set(vals)):
        return
    pickings = records.picking_id
    if vals and vals.get('picking_id'):
        pickings |= records.env['stock.picking'].browse(vals['picking_id'])
    if pickings:
        pickings._approval_check_not_locked()
        pickings._approval_reset_after_change()


class StockMove(models.Model):
    _inherit = 'stock.move'

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        _guard(moves)
        return moves

    def write(self, vals):
        _guard(self, vals, LOCKED_MOVE_FIELDS)
        return super().write(vals)

    def unlink(self):
        _guard(self)
        return super().unlink()


class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        _guard(lines)
        return lines

    def write(self, vals):
        _guard(self, vals, LOCKED_MOVE_LINE_FIELDS)
        return super().write(vals)

    def unlink(self):
        _guard(self)
        return super().unlink()

    def _free_reservation(self, *args, **kwargs):
        # Physical stock corrections (inventory adjustments, scraps...) may still
        # release reservations: reality wins over a pending approval.
        return super(StockMoveLine, self.with_context(picking_approval_bypass=True))._free_reservation(
            *args, **kwargs)
