# Advanced Stock Picking Approval (`stock_picking_approval_advanced`)

Odoo 17.0 · Community & Enterprise · depends on `stock`, `mail` · LGPL-3 · Author: Ibrahim Yahia

Routes the validation of Receipts, Delivery Orders and Internal Transfers through a
rule-based, sequential multi-tier approval workflow.

## How it works

1. A user clicks **Validate**. If an active approval rule matches the transfer, nothing is
   executed: drafts are confirmed, quantities are prepared and sanity-checked, and the
   transfer moves to **Waiting for Approval** (`state = 'to_approve'`).
2. While waiting, operations and reservations are frozen (form, ORM and RPC level):
   moves/move lines cannot be created, edited or deleted, `do_unreserve` and
   `action_assign` are blocked, and `_action_done` refuses to run.
   Physical stock corrections (`_free_reservation`) are still allowed.
3. Tier 1 approvers get an email and a *Transfer Approval* to-do activity. Each tier is
   signed by any one of its approvers (comment + optional drawn signature), then the next
   tier is notified.
4. On the final signature the transfer is validated automatically (or returns to Ready
   when *Validate on Final Approval* is off). The requester is emailed.
5. A rejection requires a reason, cancels the remaining tiers, returns the transfer to
   Ready and emails the requester. Validating again starts a new cycle; history is kept.

## Configuration

*Inventory › Configuration › Approval Rules* (group **Transfer Approvals / Administrator**)

| Setting | Effect |
|---|---|
| Receipts / Delivery Orders / Internal Transfers | Kinds of operation covered |
| Operation Types, Warehouses | Optional narrowing |
| Requesting Users / Groups | Rule only applies when these users validate |
| Approval Tiers | Ordered tiers; approvers by user and/or group (implied groups included) |
| Allow Self-Approval | Off = separation of duties (requester excluded from all tiers) |
| Require Drawn Signature | Approvers must draw a signature |
| Validate on Final Approval | Auto-execute the transfer after the last tier |
| Email Notifications / To-Do Activities | Notification channels |

Rules are evaluated by sequence; the first match wins.

## Security

* **Transfer Approvals / Approver** – implies Inventory User; can be used as a tier group.
* **Transfer Approvals / Administrator** – manages rules and can sign any tier (override).
* Approval fields and sign-off lines are written only by the workflow engine.
* Multi-company record rules on rules, tiers and sign-offs.

## Other behaviour

* Changing the operations of an *approved* (not yet done) transfer withdraws the approval.
* Backorders start with no approval and go through their own cycle.
* Recall: the requester or an administrator can withdraw a pending request.
* Menu *Inventory › Operations › Transfers to Approve* and the search filters
  *Waiting for Approval*, *Awaiting My Approval*, *Approval Rejected*.

## Tests

```
odoo-bin -d <db> -i stock_picking_approval_advanced --test-tags /stock_picking_approval_advanced --stop-after-init
```

28 `TransactionCase` tests cover state interception, locking, sequential tiers,
group-based approvers, permission enforcement, self-approval, rejection reasons,
recall/cancel, rule scoping and priority, signatures, notifications and backorders.
