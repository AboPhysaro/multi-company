# Copyright 2017 ACSONE SA/NV
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

{
    "name": "Mail Template Multi Company",
    "version": "17.0.1.0.0",
    "license": "AGPL-3",
    "author": "ACSONE SA/NV," "Odoo Community Association (OCA)",
    "website": "https://github.com/AboPhysaro/multi-company/tree/17.0/mail_template_multi_company",
    "depends": ["mail"],
    "post_init_hook": "post_init_hook",
    "data": ["security/mail_template.xml", "views/mail_template.xml"],
    "development_status": "Beta",
    "maintainers": ["Olivier-LAURENT"],
    "application": True,
    "summary": "Allow the creation of mail templates that are accessible only within a predefined company",
}
