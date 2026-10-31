# -*- coding: utf-8 -*-
{
    'name': 'Advanced Stock Picking Approval',
    'version': '17.0.1.0.0',
    'category': 'Inventory/Inventory',
    'summary': 'Multi-level, rule-based approval workflow for Receipts, '
               'Delivery Orders and Internal Transfers',
    'description': """
Advanced Stock Picking Approval
===============================
Intercepts transfer validation and routes it through a configurable,
sequential multi-tier approval workflow.

* "Waiting for Approval" state with locked reservations and operations
* Dynamic rules by operation type, warehouse, requesting users and groups
* Sequential tiers with user- and group-based approvers
* Digital sign-off (comment + optional drawn signature) per tier
* Mandatory rejection reasons and full approval history per cycle
* Email alerts and to-do activities for approvers, outcome emails for requesters
* Separation of duties (no self-approval) and approval-administrator override
* Arabic translation included
    """,
    'author': 'Ibrahim Yahia',
    'website': 'https://www.linkedin.com/in/ibrahim-yahia',
    'license': 'LGPL-3',
    'depends': ['stock', 'mail'],
    'data': [
        'security/stock_picking_approval_security.xml',
        'security/ir.model.access.csv',
        'data/mail_activity_type_data.xml',
        'data/mail_template_data.xml',
        'wizard/stock_picking_approval_wizard_views.xml',
        'views/stock_picking_approval_rule_views.xml',
        'views/stock_picking_views.xml',
        'views/menus.xml',
    ],
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': False,
}
