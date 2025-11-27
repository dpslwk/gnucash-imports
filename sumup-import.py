#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""" SumUp import for Nottingham Hackspace

    Requires piecash, requests, requests_oauthlib

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
from requests_oauthlib import OAuth2Session
from datetime import datetime, timedelta
import pytz
from decimal import Decimal
import configparser
import logging
import warnings
from sqlalchemy import exc as sa_exc

warnings.simplefilter("ignore", category=sa_exc.SAWarning)

# setup initial Logging
logging.getLogger().setLevel(logging.NOTSET)
logger = logging.getLogger('SumUp import')
_ch = logging.StreamHandler()
_ch.setLevel(logging.INFO)    # this should be WARN by default
_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
_ch.setFormatter(_formatter)
logger.addHandler(_ch)
_fh = logging.FileHandler('sumup-import.log')
_fh.setFormatter(_formatter)
_fh.setLevel(logging.INFO)
logger.addHandler(_fh)

logger.info("Importing SumUp charges")
configFilename = 'imports.cfg'
config = configparser.ConfigParser()
config.read(configFilename)

# sumup api keys
client_id = config['SumUp']['client_id']
client_secret = config['SumUp']['client_secret']
authorization_base_url = 'https://api.sumup.com/authorize'
token_url = 'https://api.sumup.com/token'
refresh_url = 'https://api.sumup.com/token'
token = {
    'access_token': config['SumUp']['access_token'],
    'refresh_token': config['SumUp']['refresh_token'],
    'token_type': 'Bearer',
    'expires_at': float(config['SumUp']['expires_at']),
}
extra = {
    'client_id': client_id,
    'client_secret': client_secret,
}
# After updating the token you will most likely want to save it.
def token_saver(token):
    logger.info("Saving new token")
    # update config with new token
    config['SumUp']['access_token'] = token['access_token']
    config['SumUp']['refresh_token'] = token['refresh_token']
    config['SumUp']['expires_at'] = str(token['expires_at'])
    # and save it to the config
    with open(configFilename, 'w') as configfile:
        config.write(configfile)

# gnucash book we are working with
bookPath = config['GNUCash']['book_path']
logger.info("Into GnuCash book: {}".format(bookPath))

# work out how far back we need to go
lastRunDate = datetime.strptime(config['SumUp']['last_run'], '%Y-%m-%dT%H:%M:%S.%f')
logger.info('Last Run: ' + str(lastRunDate))
# go back 24 hours from our last run to make sure we don't miss anything
oldestTime = (lastRunDate - timedelta(days=1)).isoformat()

# setup request client
client = OAuth2Session(client_id, token=token, auto_refresh_url=refresh_url,
    auto_refresh_kwargs=extra, token_updater=token_saver)
# fetch
data = {
    'order': 'descending',
    'limit': 1000,
    'oldest_time': oldestTime
}
r = client.get('https://api.sumup.com/v0.1/me/transactions/history', data=data)
sumUpTransactions = r.json(parse_float=Decimal)
logger.info("Fetched {} transactions from SumUp".format(len(sumUpTransactions['items'])))

if (len(sumUpTransactions['items']) == 0):
    logger.info("No transactions to import")
    exit()

with open_book(bookPath, readonly=False) as book:
    # grab the accounts we need
    sumUpAccount = book.accounts(fullname="Assets:Current Assets:SumUp")
    feeExpenseAccount = book.accounts(fullname="Expenses:Bank Service Charge")
    miscellaneousExpenseAccount = book.accounts(fullname="Expenses:Miscellaneous")
    snackspaceIncomeAccount = book.accounts(fullname="Income:Snackspace")
    donationsIncomeAccount = book.accounts(fullname="Income:Donations")
    gbp = sumUpAccount.commodity

    for sumUpTransaction in sumUpTransactions['items']:
        if sumUpTransaction['status'] != 'SUCCESSFUL':
            logger.info("Skipped Failed: {}".format(sumUpTransaction['id']))
            continue

        try:
            # see if we have already recorded this transaction
            book.transactions.get(num=sumUpTransaction['id'])
            logger.info("Skipped already recorded: {}".format(sumUpTransaction['id'] ))
            continue
        except KeyError:
            # we have not yet recorded this txn_ id
            pass

        # fetch the full transaction from sumup
        data = {
            'id': sumUpTransaction['id']
        }

        r = client.get('https://api.sumup.com/v0.1/me/transactions', data=data)
        transaction = r.json(parse_float=Decimal)
        if (len(transaction['events']) == 0):
            logger.warn("Skipped no events for: {}".format(sumUpTransaction['id']))
            continue
        elif (len(transaction['events']) > 1):
            logger.warn("Skipped more than one event for: {}".format(sumUpTransaction['id']))
            continue

        # pull out some generic details for this transaction
        amount = -1 * sumUpTransaction['amount']
        fee = transaction['events'][0]['fee_amount']
        net = transaction['events'][0]['amount']
        createdAt = pytz.utc.localize(datetime.strptime(sumUpTransaction['timestamp'], '%Y-%m-%dT%H:%M:%S.%fZ')).astimezone(pytz.timezone("Europe/London"))

        if sumUpTransaction['type'] == 'PAYMENT':
            # build description for gnu cash
            description = "SumUp: {} ({})".format(sumUpTransaction['transaction_code'], transaction['card']['last_4_digits'])

            # which account are we assigning this to
            toAccount = snackspaceIncomeAccount

            Transaction(currency=gbp,
                        enter_date=createdAt,
                        post_date=createdAt.date(),
                        num=sumUpTransaction['id'],
                        description=description,
                        splits=[
                            Split(account=sumUpAccount, value=net),
                            Split(account=toAccount, value=amount),
                            Split(account=feeExpenseAccount, value=fee)
                        ])

            logger.info("Saved charge: {}, {}, {}".format(sumUpTransaction['id'], createdAt.date(), description))
        elif sumUpTransaction['type'] == 'CHARGE_BACK':
            description = "SumUp: {}".format(sumUpTransaction['transaction_code'])
            if net < 0:
                toAccount = miscellaneousExpenseAccount
            else:
                toAccount = snackspaceIncomeAccount

            Transaction(currency=gbp,
                        enter_date=createdAt,
                        post_date=createdAt.date(),
                        num=sumUpTransaction['id'],
                        description=description,
                        splits=[
                            Split(account=sumUpAccount, value=net),
                            Split(account=toAccount, value=amount),
                            Split(account=feeExpenseAccount, value=fee)
                        ])

            logger.info("Saved charge back: {}, {}, {}".foramt(sumUpTransaction['id'], createdAt.date(), sumUpTransaction['transaction_code']))
        elif sumUpTransaction['type'] == 'REFUND':
            description = "SumUp: {}, {}".format(sumUpTransaction['transaction_code'], sumUpTransaction['transaction_id'])

            toAccount = miscellaneousExpenseAccount

            Transaction(currency=gbp,
                        enter_date=createdAt,
                        post_date=createdAt.date(),
                        num=sumUpTransaction['id'],
                        description=sumUpTransaction['transaction_code'],
                        splits=[
                            Split(account=sumUpAccount, value=net),
                            Split(account=toAccount, value=amount),
                        ])

            logger.info("Saved refund: {}, {}, {}".format(sumUpTransaction['id'], createdAt.date(), description))
        else:
            # don't know what type this is
            logger.info("Skipped Unknown type: {}, {}, {}".format(sumUpTransaction['type'], sumUpTransaction['id'], createdAt.date()))

    # save the book
    if not book.is_saved:
        book.save()

    # update last run date
    config['SumUp']['last_run'] = datetime.now().isoformat()

    # and save it to the config
    with open(configFilename, 'w') as configfile:
        config.write(configfile)
