#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""" Monthly VAT report for Nottingham Hackspace

    Intended to be called by hand with year and month, generates a csv file

    Requires piecash, python-dateutil

    Author: Matt Lloyd

    Copyright (c) 2025 Matt Lloyd

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
# from jinja2 import Environment, FileSystemLoader, select_autoescape
# import mwclient
from datetime import datetime, timedelta, date
from dateutil.parser import isoparse
from dateutil.relativedelta import relativedelta
from calendar import monthrange
import pytz
from decimal import Decimal
import argparse
import configparser
import logging
import os
import sys
import json
import hashlib
import csv
import io
import warnings
from sqlalchemy import exc as sa_exc

warnings.simplefilter("ignore", category=sa_exc.SAWarning)

dirname = os.path.dirname(os.path.realpath(__file__))

# setup initial Logging
logging.getLogger().setLevel(logging.NOTSET)
logger = logging.getLogger('VAT Report')
# _ch = logging.StreamHandler()
# _ch.setLevel(logging.WARN)    # this should be WARN by default
_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# _ch.setFormatter(_formatter)
# logger.addHandler(_ch)
_fh = logging.FileHandler(os.path.join(dirname, 'wiki-report.log'))
_fh.setFormatter(_formatter)
_fh.setLevel(logging.INFO)
logger.addHandler(_fh)

parser = argparse.ArgumentParser(
    prog='VAT Report',
    description='Monthly wiki report for  Nottingham Hackspace',)

parser.add_argument('enddate',
    help='The End Date, day will be adjusted to end of the month , ISOformat YYYY-MM-DD (Inclusive)',
    # required=True,
    type=isoparse)

args = parser.parse_args()

endDate = args.enddate.date()
endOfMonth = endDate.replace(day=monthrange(endDate.year, endDate.month)[1])
endOfPerviousMonth = endDate.replace(day=1) - timedelta(days=1)

vatPeriodEnd = endOfMonth
vatPeriodStart = (endDate - relativedelta(years=1)).replace(day=1) - timedelta(days=1)

financeStartYear = date(2011, 10, 1)

# env = Environment(
#     loader=FileSystemLoader("templates"),
#     autoescape=select_autoescape()
# )
# template = env.get_template("vat-report.jinja")

logger.info(f"Generating VAT Report for {endOfMonth:%Y-%m}")
configFilename = os.path.join(dirname, 'imports.cfg')
config = configparser.ConfigParser()
config.read(configFilename)

# gnucash book we are working with
bookPath = config['GNUCash']['book_path']
outputDir = config['VATReport']['export_path']

lastRunDate = datetime.strptime(config['VATReport']['last_run'], '%Y-%m-%dT%H:%M:%S.%f')
logger.info('Last Run: ' + str(lastRunDate))

logger.info("GnuCash book: {}".format(bookPath))

def delta(acc, recurse=True):
    return acc.get_balance(recurse=recurse, at_date=endOfMonth) - acc.get_balance(recurse=recurse, at_date=endOfPerviousMonth)

def delta_for(acc, start, end, recurse=True):
    return acc.get_balance(recurse=recurse, at_date=end) - acc.get_balance(recurse=recurse, at_date=start)

def formatGBP(amount):
    return f"£{amount:,}"

def child_deltas(account):
    return {
        child.name: formatGBP(delta(child, False))
        for child in account.children
    }

def write_file(name, data):
    with open(name, 'w') as fp:
        fp.write(data)

def dict_to_two_row_csv(d):
    output = io.StringIO()
    writer = csv.writer(output)

    # First row: keys
    writer.writerow(d.keys())
    # Second row: values
    writer.writerow(d.values())

    return output.getvalue()


with open_book(bookPath, readonly=True) as book:
    income_accounts = [
        acc for acc in book.accounts
        if acc.type == 'INCOME'
    ]

    income_accounts = sorted(income_accounts, key=lambda a: a.fullname)

    income_amounts = {
        account.fullname: formatGBP(delta(account, False))
        for account in income_accounts
    }

    csvString = dict_to_two_row_csv(
        {'Date': f"{endOfMonth:%Y/%m/%d}"} | income_amounts
    )

    reportName = f"VAT {endOfMonth:%Y-%m %B}"
    print(csvString)

    write_file(
        os.path.join(outputDir, f"{reportName}.csv"),
        csvString
    )

    # update last run date
    config['VATReport']['last_run'] = datetime.now().isoformat()

    # and save it to the config
    with open(configFilename, 'w') as configfile:
        config.write(configfile)
