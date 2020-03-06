#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""" TSB import for Nottingham Hackspace

    Intended to be called from tsbscrape and passed a JOSN string for each transaction

    Requires piecash, python-dateutil

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
from datetime import datetime, timedelta
from dateutil.parser import isoparse
import pytz
from decimal import Decimal
import configparser
import logging
import os
import sys
import json
import hashlib

dirname = os.path.dirname(os.path.realpath(__file__))

# setup initial Logging
logging.getLogger().setLevel(logging.NOTSET)
logger = logging.getLogger('TSB import')
# _ch = logging.StreamHandler()
# _ch.setLevel(logging.WARN)    # this should be WARN by default
_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# _ch.setFormatter(_formatter)
# logger.addHandler(_ch)
_fh = logging.FileHandler(os.path.join(dirname, 'tsb-import.log'))
_fh.setFormatter(_formatter)
_fh.setLevel(logging.INFO)
logger.addHandler(_fh)

logger.info("Importing TSB charges")
configFilename = os.path.join(dirname, 'imports.cfg')
config = configparser.ConfigParser()
config.read(configFilename)

# gnucash book we are working with
bookPath = config['GNUCash']['book_path']

# get current rent amounts from config
f6Rent = int(config['GNUCash']['f6_rent'])
g456Rent = int(config['GNUCash']['g456_rent'])
logger.info("Into GnuCash book: {}".format(bookPath))

with open_book(bookPath, readonly=False) as book:
    tsbAccount = book.accounts(fullname="Assets:Current Assets:TSB Account")
    # grab extra accounts we need
    g456Account = book.accounts(fullname="Expenses:Bizspace Rent:G4,5,6")
    electricAccont = book.accounts(fullname="Expenses:Utilities:Electric")
    intrestAccount = book.accounts(fullname="Expenses:Member Loan Repayments:Interest Payments")
    gbp = tsbAccount.commodity

    importCount = 0
    # expect one JSON transaction per line
    # {
    #   date: '2020-01-06T00:00:00.000Z',
    #   description: 'STRIPE PAYMENTS UK LTD STRIPE',
    #   in: 3904,
    #   out: null,
    #   amount: 3904,
    #   transferAccount: 'Assets:Current Assets:Stripe'
    # }
    for line in sys.stdin:
        transaction = json.loads(line)
        logger.info("Got Transaction to import: {}".format(json.dumps(transaction)))

        # find the transferAccount
        try:
            transferAccount = book.accounts(fullname=transaction['transferAccount'])
        except KeyError as e:
            logger.warn("Unable to find Account: {}".format(transaction['transferAccount']))
            print(json.dumps("Transaction not imported: Unable to find Account: {}".format(hashString)))
        else:
            # build hash for transaction to use a an UID
            hashString = "{}:{};{}".format(transaction['date'], transaction['description'], transaction['amount'])
            hashHex = hashlib.sha256(hashString.encode()).hexdigest()

            try:
                # see if we have already recorded this transaction
                book.transactions.get(num=hashHex)
                logger.info("Skipped already recorded: {} {}".format(hashString, hashHex))
                print(json.dumps("Skipped already recorded: {}".format(hashString)))
                continue
            except KeyError:
                # we have not yet recorded this txn_ id
                pass

            # pull out some generic details for this transaction
            amount = Decimal(transaction['amount'])/100
            createdAt = isoparse(transaction['date']).astimezone(pytz.timezone("Europe/London"))

            splits = []

            if (transaction['transferAccount'] == 'Expenses:Bizspace Rent:F6' and (-1*transaction['amount']) > (f6Rent+g456Rent)):
                # pre slipt the rent
                # prep rent ammounts
                f6Amount = Decimal(f6Rent)/100
                g456Amount = Decimal(g456Rent)/100

                # calculate electric amount
                electricAmount = Decimal(transaction['amount'] + f6Rent + g456Rent)/100

                splits=[
                    Split(account=tsbAccount, value=amount),
                    Split(account=transferAccount, value=f6Amount), #F6
                    Split(account=g456Account, value=g456Amount),
                    Split(account=electricAccont, value=-1*electricAmount)
                ];
            elif (transaction['transferAccount'] == 'Liabilities:Membership Loan Payable'):
                # per split loan repayments
                # 20.83   3.34    24.17
                # 83.33   13.34   96.67
                # 41.67   6.66    48.33
                # 104.17  16.66   120.83
                payableAmount = -1*amount
                intrestAmount = Decimal(0)
                if (transaction['amount'] == -2417):
                    payableAmount = Decimal(2083)/100
                    intrestAmount = Decimal(334)/100
                elif (transaction['amount'] == -9667):
                    payableAmount = Decimal(8333)/100
                    intrestAmount = Decimal(1334)/100
                elif (transaction['amount'] == -4833):
                    payableAmount = Decimal(4167)/100
                    intrestAmount = Decimal(666)/100
                elif (transaction['amount'] == 1-12083):
                    payableAmount = Decimal(10417)/100
                    intrestAmount = Decimal(1666)/100
                else:
                    logger.warn("Unmatched loan repayment amount")

                splits=[
                    Split(account=tsbAccount, value=amount),
                    Split(account=transferAccount, value=payableAmount),
                    Split(account=intrestAccount, value=intrestAmount)
                ];
            else:
                # just the normal splits
                splits = [
                    Split(account=tsbAccount, value=amount),
                    Split(account=transferAccount, value=-1*amount)
                    ];

            # now we have the splits we can create the trasnaction
            Transaction(currency=gbp,
                        enter_date=createdAt,
                        post_date=createdAt.date(),
                        num=hashHex,
                        description=transaction['description'],
                        splits=splits
                        )

            # save the book
            if not book.is_saved:
                book.save()

            importCount += 1
            logger.info("Imported, Total count: {}".format(importCount))
            print(json.dumps("Imported, Total count: {}".format(importCount)))

