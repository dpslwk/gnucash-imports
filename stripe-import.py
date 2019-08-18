#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""" Stripe import for Nottingham Hackspace

    Requires stripe, piecash

    Author: Matt Lloyd

    Copyright (c) 2019 Matt Lloyd

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.

"""
from piecash import open_book, Transaction, Split, GncImbalanceError, ledger
import stripe
from datetime import datetime, timedelta
import pytz
from decimal import Decimal
import configparser
import logging

# setup initial Logging
logging.getLogger().setLevel(logging.NOTSET)
logger = logging.getLogger('Stripe import')
_ch = logging.StreamHandler()
_ch.setLevel(logging.INFO)    # this should be WARN by default
_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
_ch.setFormatter(_formatter)
logger.addHandler(_ch)
_fh = logging.FileHandler('stripe-import.log')
_fh.setFormatter(_formatter)
_fh.setLevel(logging.INFO)
logger.addHandler(_fh)

logger.info("Importing Stripe charges")
configFilename = 'imports.cfg'
config = configparser.ConfigParser()
config.read(configFilename)

# load stripe api key from config
stripe.api_key = config['Stripe']['api_key']

# gnucash book we are working with
bookPath = config['GNUCash']['book_path']
logger.info("Into GnuCash book: {}".format(bookPath))

# work out how far back we need to go
lastRunDate = datetime.strptime(config['Stripe']['last_run'], '%Y-%m-%dT%H:%M:%S.%f')
logger.info('Last Run: ' + str(lastRunDate))
# go back 24 hours from our last run to make sure we don't miss anything
fetchTimestamp = int((lastRunDate - timedelta(days=1)).timestamp())

stripeTransactions = stripe.BalanceTransaction.list(limit=1000, created={'gt': fetchTimestamp}, expand=['data.source'])
logger.info("Fetched {} transactions from Stripe".format(len(stripeTransactions)))

if (len(stripeTransactions) == 0):
    logger.info("No transactions to import")
    exit()

with open_book(bookPath, readonly=False) as book:
    # grab the accounts we need
    stripeAccount = book.accounts(fullname="Assets:Current Assets:Stripe")
    feeExpenseAccount = book.accounts(fullname="Expenses:Bank Service Charge")
    miscellaneousExpenseAccount = book.accounts(fullname="Expenses:Miscellaneous")
    snackspaceIncomeAccount = book.accounts(fullname="Income:Snackspace")
    donationsIncomeAccount = book.accounts(fullname="Income:Donations")
    gbp = stripeAccount.commodity

    for stripeTransaction in reversed(stripeTransactions.to_dict()['data']):
        try:
            # see if we have already recorded this transaction
            book.transactions.get(num=stripeTransaction.id)
            logger.info("Skipped already recorded: {}".format(stripeTransaction.id ))
            continue
        except KeyError:
            # we have not yet recorded this txn_ id
            pass

        # pull out some generic details for this transaction
        amount = Decimal(-1 * stripeTransaction.amount)/100
        fee = Decimal(stripeTransaction.fee)/100
        net = Decimal(stripeTransaction.net)/100
        createdAt = pytz.utc.localize(datetime.fromtimestamp(stripeTransaction.created)).astimezone(pytz.timezone("Europe/London"))

        if stripeTransaction.type == 'charge':
            # build description for gnu cash
            description = "Stripe "
            if 'statement_descriptor' in stripeTransaction.source:
                description += stripeTransaction.source.statement_descriptor + ": "
            elif 'statement_descriptor_suffix' in stripeTransaction.source:
                description += stripeTransaction.source.statement_descriptor_suffix + ": "
            if 'user_id' in stripeTransaction.source.metadata:
                description += stripeTransaction.source.metadata.user_id + ", "
            if 'name' in stripeTransaction.source.billing_details:
                if stripeTransaction.source.billing_details.name is not None:
                    description += stripeTransaction.source.billing_details.name
            description += " (" + stripeTransaction.source.payment_method_details.card.last4 + ")"

            # which account are we assigning this to
            toAccount = donationsIncomeAccount
            if 'type' in stripeTransaction.source.metadata:
                if stripeTransaction.source.metadata.type.upper() == 'SNACKSPACE':
                    toAccount = snackspaceIncomeAccount
            elif stripeTransaction.source.statement_descriptor == 'Snackspace':
                toAccount = snackspaceIncomeAccount
            elif stripeTransaction.source.statement_descriptor_suffix == 'Snackspace':
                toAccount = snackspaceIncomeAccount

            Transaction(currency=gbp,
                        enter_date=createdAt,
                        post_date=createdAt.date(),
                        num=stripeTransaction.id,
                        description=description,
                        splits=[
                            Split(account=stripeAccount, value=net),
                            Split(account=toAccount, value=amount),
                            Split(account=feeExpenseAccount, value=fee)
                        ])

            logger.info("Saved charge: {}, {}, {}".format(stripeTransaction.id, createdAt.date(), description))
        elif stripeTransaction.type == 'adjustment':
            description = "Stripe: " + stripeTransaction.description
            if net < 0:
                toAccount = miscellaneousExpenseAccount
            else:
                stripeCharge = stripe.Charge.retrieve(stripeTransaction.source.charge)

                toAccount = donationsIncomeAccount
                if 'type' in stripeCharge.metadata:
                    if stripeCharge.metadata.type.upper() == 'SNACKSPACE':
                        toAccount = snackspaceIncomeAccount

            Transaction(currency=gbp,
                        enter_date=createdAt,
                        post_date=createdAt.date(),
                        num=stripeTransaction.id,
                        description=description,
                        splits=[
                            Split(account=stripeAccount, value=net),
                            Split(account=toAccount, value=amount),
                            Split(account=feeExpenseAccount, value=fee)
                        ])

            logger.info("Saved adjustment: {}, {}, {}".format(stripeTransaction.id, createdAt.date(), stripeTransaction.description))
        elif stripeTransaction.type == 'refund':
            description = "Stripe: " + stripeTransaction.description + ": " + stripeTransaction.source.charge

            toAccount = miscellaneousExpenseAccount

            Transaction(currency=gbp,
                        enter_date=createdAt,
                        post_date=createdAt.date(),
                        num=stripeTransaction.id,
                        description=stripeTransaction.description,
                        splits=[
                            Split(account=stripeAccount, value=net),
                            Split(account=toAccount, value=amount),
                        ])

            logger.info("Saved refund: {}, {}, {}".format(stripeTransaction.id, createdAt.date(), description))
        elif stripeTransaction.type == 'payout':
            # payouts will be recorded when they show up in our TSB CSV
            logger.info("Skipped Payout: {}, {}, £{}".format(stripeTransaction.id, createdAt.date(), amount))
        else:
            # don't know what type this is
            logger.info("Skipped Unknown type: {}, {}, {}".format(stripeTransaction.type, stripeTransaction.id, createdAt.date()))

    # save the book
    if not book.is_saved:
        book.save()

    # update last run date
    config['Stripe']['last_run'] = datetime.now().isoformat()

    # and save it to the config
    with open(configFilename, 'w') as configfile:
        config.write(configfile)
