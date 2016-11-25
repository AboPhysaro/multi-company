# -*- coding: utf-8 -*-
from openerp import api, fields, models, _
from openerp.exceptions import Warning as UserError
from openerp.tools import float_compare


class AccountInvoice(models.Model):

    _inherit = 'account.invoice'

    auto_generated = fields.Boolean(string='Auto Generated Document',
                                    copy=False, default=False)
    auto_invoice_id = fields.Many2one('account.invoice',
                                      string='Source Invoice',
                                      readonly=True, copy=False,
                                      _prefetch=False)

    @api.multi
    def invoice_validate(self):
        """ Validated invoice generate cross invoice base on company rules """
        for src_invoice in self:
            # do not consider invoices that have already been auto-generated,
            # nor the invoices that were already validated in the past
            dest_company = self.env['res.company']._find_company_from_partner(
                src_invoice.partner_id.id)
            if (dest_company and not src_invoice.auto_generated):
                if src_invoice.type == 'out_invoice':
                    src_invoice.inter_company_create_invoice(dest_company,
                                                             'in_invoice',
                                                             'purchase')
                elif src_invoice.type == 'in_invoice':
                    src_invoice.inter_company_create_invoice(dest_company,
                                                             'out_invoice',
                                                             'sale')
                elif src_invoice.type == 'out_refund':
                    src_invoice.inter_company_create_invoice(dest_company,
                                                             'in_refund',
                                                             'purchase_refund')
                elif src_invoice.type == 'in_refund':
                    src_invoice.inter_company_create_invoice(dest_company,
                                                             'out_refund',
                                                             'sale_refund')
        return super(AccountInvoice, self).invoice_validate()

    @api.multi
    def inter_company_create_invoice(
            self, dest_company, dest_inv_type, dest_journal_type):
        """ create an invoice for the given company : it will copy "
        "the invoice lines in the new
            invoice. The intercompany user is the author of the new invoice.
            :param dest_company : the company of the created invoice
            :rtype dest_company : res.company record
            :param dest_inv_type : the type of the invoice "
            "('in_refund', 'out_refund', 'in_invoice', ...)
            :rtype dest_inv_type : string
            :param dest_journal_type : the type of the journal "
            "to register the invoice
            :rtype dest_journal_type : string
        """
        self.ensure_one()
        AccountInvoice = self.env['account.invoice']

        # find user for creating the invoice from company
        intercompany_uid = (dest_company.intercompany_user_id and
                            dest_company.intercompany_user_id.id or False)
        if not intercompany_uid:
            raise UserError(_(
                'Provide one user for intercompany relation for %s ')
                % dest_company.name)

        # check intercompany user access rights
        if not AccountInvoice.sudo(intercompany_uid).check_access_rights(
                'create', raise_exception=False):
            raise UserError(_(
                "Inter company user of company %s doesn't have enough "
                "access rights") % dest_company.name)

        # check intercompany product
        for line in self.invoice_line:
            try:
                line.product_id.sudo(intercompany_uid).read()
            except:
                raise UserError(_(
                    "You cannot create invoice in company '%s' because "
                    "product '%s' is not intercompany")
                    % (dest_company.name, line.product_id.name))

        # if an invoice has already been genereted
        # delete it and force the same number
        inter_invoice = self.sudo(intercompany_uid).search(
            [('auto_invoice_id', '=', self.id)])
        force_number = False
        if inter_invoice:
            force_number = inter_invoice.internal_number
            inter_invoice.internal_number = False
            inter_invoice.unlink()
        context = self._context.copy()
        context['force_company'] = dest_company.id
        src_company_partner_id = self.company_id.partner_id
        dest_invoice_lines = []
        # create invoice, as the intercompany user
        dest_invoice_vals = self.with_context(
            context).sudo()._prepare_invoice_data(
                dest_invoice_lines, dest_inv_type,
                dest_journal_type, dest_company)
        if force_number:
            dest_invoice_vals['internal_number'] = force_number
        for src_line in self.invoice_line:
            if not src_line.product_id:
                raise UserError(_(
                    "The invoice line '%s' doesn't have a product. "
                    "All invoice lines should have a product for "
                    "inter-company invoices.") % src_line.name)
            # get invoice line data from product onchange
            dest_line_data = src_line.with_context(context).sudo(
                intercompany_uid).product_id_change(
                    src_line.product_id.id,
                    src_line.product_id.uom_id.id,
                    qty=src_line.quantity,
                    name='',
                    type=dest_inv_type,
                    partner_id=src_company_partner_id.id,
                    fposition_id=dest_invoice_vals['fiscal_position'],
                    company_id=dest_company.id)
            # create invoice line, as the intercompany user
            dest_inv_line_data = self.sudo()._prepare_invoice_line_data(
                dest_line_data, src_line)
            dest_invoice_lines.append((0, 0, dest_inv_line_data))
        dest_invoice = self.with_context(context).sudo(
            intercompany_uid).create(dest_invoice_vals)
        precision = self.env['decimal.precision'].precision_get('Account')
        if (dest_company.invoice_auto_validation and
                not float_compare(self.amount_total,
                                  dest_invoice.amount_total,
                                  precision_digits=precision)):
            dest_invoice.signal_workflow('invoice_open')
        else:
            dest_invoice.button_reset_taxes()

        if float_compare(self.amount_total, dest_invoice.amount_total,
                         precision_digits=precision):
            body = (_(
                "WARNING!!!!! Failure in the inter-company invoice creation "
                "process: the total amount of this invoice is %s but the "
                "total amount in the company %s is %s")
                % (dest_invoice.amount_total, self.company_id.name,
                   self.amount_total))
            dest_invoice.message_post(body=body)

        sales = self.env['sale.order'].search([
            ('invoice_ids', '=', self.id),
            ('auto_purchase_order_id', '!=', False)])
        for sale in sales:
            purchase = sale.auto_purchase_order_id.sudo(intercompany_uid)
            purchase.invoice_ids = [(4, dest_invoice.id)]
            if dest_invoice.state not in ['draft', 'cancel']:
                        purchase.order_line.write({'invoiced': True})

            for sale_line in sale.order_line:
                purchase_line = (sale_line.auto_purchase_line_id.
                                 sudo(intercompany_uid))
                for invoice_line in dest_invoice.invoice_line:
                    if (sale_line.invoice_lines == invoice_line.
                            auto_invoice_line_id):
                        purchase_line.invoice_lines = [
                            (4, invoice_line.id)]
        return True

    @api.multi
    def _prepare_invoice_data(self,
                              dest_invoice_lines, dest_inv_type,
                              dest_journal_type, dest_company):
        """ Generate invoice values
            :param dest_invoice_lines : the list of invoice lines to create
            :rtype dest_invoice_line_ids : list of tuples
            :param dest_inv_type : the type of the invoice to prepare "
            "the values
            :param dest_journal_type : type of the journal "
            "to register the invoice_line_ids
            :rtype dest_journal_type : string
            :rtype dest_company : res.company record
        """
        self.ensure_one()
        # find the correct journal
        dest_journal = self.env['account.journal'].search([
            ('type', '=', dest_journal_type),
            ('company_id', '=', dest_company.id)
        ], limit=1)
        if not dest_journal:
            raise UserError(_(
                'Please define %s journal for this company: "%s" (id:%d).')
                % (dest_journal_type, dest_company.name, dest_company.id))

        # find periods of supplier company
        context = self._context.copy()
        context['company_id'] = dest_company.id
        dest_period_ids = self.env['account.period'].with_context(
            context).find(self.date_invoice)

        # find account, payment term, fiscal position, bank.
        dest_partner_data = self.onchange_partner_id(
            dest_inv_type, self.company_id.partner_id.id,
            company_id=dest_company.id)
        if not self.currency_id.company_id:
            # currency shared between companies
            dest_currency_id = self.currency_id.id
        else:
            # currency not shared between companies
            dest_curs = self.env['res.currency'].with_context(context).search([
                ('name', '=', self.currency_id.name),
                ('company_id', '=', dest_company.id),
            ], limit=1)
            if not dest_curs:
                raise UserError(_(
                    "Could not find the currency '%s' in the company '%s'")
                    % (self.currency_id.name, dest_company.name_get()[0][1]))
            dest_currency_id = dest_curs[0].id
        return {
            'name': self.name,
            # TODO : not sure !!
            'origin': self.company_id.name + _(' Invoice: ') + str(
                self.number),
            'supplier_invoice_number': self.number,
            'check_total': self.amount_total,
            'type': dest_inv_type,
            'date_invoice': self.date_invoice,
            'reference': self.reference,
            'account_id': dest_partner_data['value'].get('account_id', False),
            'partner_id': self.company_id.partner_id.id,
            'journal_id': dest_journal.id,
            'invoice_line': dest_invoice_lines,
            'currency_id': dest_currency_id,
            'fiscal_position': dest_partner_data['value'].get(
                'fiscal_position', False),
            'payment_term': dest_partner_data['value'].get(
                'payment_term', False),
            'company_id': dest_company.id,
            'period_id': dest_period_ids and dest_period_ids[0].id or False,
            'partner_bank_id': dest_partner_data['value'].get(
                'partner_bank_id', False),
            'auto_generated': True,
            'auto_invoice_id': self.id,
        }

    @api.model
    def _prepare_invoice_line_data(self, dest_line_data, src_line):
        """ Generate invoice line values
            :param dest_line_data : dict of invoice line data
            :rtype dest_line_data : dict
            :param src_line : the invoice line object
            :rtype src_line : account.invoice.line record
        """
        vals = {
            'name': src_line.name,
            # TODO: it's wrong to just copy the price_unit
            # You have to check if the tax is price_include True or False
            # in source and target companies
            'price_unit': src_line.price_unit,
            'quantity': src_line.quantity,
            'discount': src_line.discount,
            'product_id': src_line.product_id.id or False,
            'uos_id': src_line.uos_id.id or False,
            'sequence': src_line.sequence,
            'invoice_line_tax_id': [(6, 0, dest_line_data['value'].get(
                'invoice_line_tax_id', []))],
            # Analytic accounts are per company
            # The problem is that we can't really "guess" the
            # analytic account to set. It probably needs to be
            # set via an inherit of this method in a custom module
            'account_analytic_id': dest_line_data['value'].get(
                'account_analytic_id'),
            'account_id': dest_line_data['value']['account_id'],
            'auto_invoice_line_id': src_line.id
        }
        return vals

    @api.multi
    def action_cancel(self):
        for invoice in self:
            company = self.env['res.company']._find_company_from_partner(
                invoice.partner_id.id)
            if (
                company and company.intercompany_user_id and not
                invoice.auto_generated
            ):
                intercompany_uid = company.intercompany_user_id.id
                for inter_invoice in self.sudo(intercompany_uid).search(
                        [('auto_invoice_id', '=', invoice.id)]):
                    inter_invoice.signal_workflow('invoice_cancel')
        return super(AccountInvoice, self).action_cancel()


class AccountInvoiceLine(models.Model):

    _inherit = 'account.invoice.line'

    auto_invoice_line_id = fields.Many2one('account.invoice.line',
                                           string='Source Invoice Line',
                                           readonly=True, copy=False,
                                           _prefetch=False)
