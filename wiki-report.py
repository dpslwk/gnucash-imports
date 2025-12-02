#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""" Monthly wiki report for Nottingham Hackspace

    Intended to be called by hand with year and month, generates a md file for the wiki page
    May one day auto create/update live wiki pages (or HMS??)

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
from jinja2 import Environment, FileSystemLoader, select_autoescape
import mwclient
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
import warnings
from sqlalchemy import exc as sa_exc

warnings.simplefilter("ignore", category=sa_exc.SAWarning)

dirname = os.path.dirname(os.path.realpath(__file__))

# setup initial Logging
logging.getLogger().setLevel(logging.NOTSET)
logger = logging.getLogger('Wiki Report')
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
    prog='Wiki Report',
    description='Monthly wiki report for  Nottingham Hackspace',)

parser.add_argument('enddate',
    help='The End Date, day will be adjusted to end of the month , ISOformat YYYY-MM-DD (Inclusive)',
    # required=True,
    type=isoparse)

args = parser.parse_args()

endDate = args.enddate.date()
endOfMonth = endDate.replace(day=monthrange(endDate.year, endDate.month)[1])
endOfPerviousMonth = endDate.replace(day=1) - timedelta(days=1)

financeStartYear = date(2011, 10, 1)
wikiYear = relativedelta(endOfMonth, financeStartYear).years + 1

env = Environment(
    loader=FileSystemLoader("templates"),
    autoescape=select_autoescape()
)
template = env.get_template("wiki-report.jinja")

logger.info(f"Generating Wiki Report for {endOfMonth:%Y-%m}")
configFilename = os.path.join(dirname, 'imports.cfg')
config = configparser.ConfigParser()
config.read(configFilename)

# gnucash book we are working with
bookPath = config['GNUCash']['book_path']
outputDir = config['WikiReport']['export_path']
siteUrl = config['WikiReport']['site_url']
username = config['WikiReport']['username']
password = config['WikiReport']['password']

lastRunDate = datetime.strptime(config['WikiReport']['last_run'], '%Y-%m-%dT%H:%M:%S.%f')
logger.info('Last Run: ' + str(lastRunDate))

logger.info("GnuCash book: {}".format(bookPath))

def delta(acc, recurse=True):
    return acc.get_balance(recurse=recurse, at_date=endOfMonth) - acc.get_balance(recurse=recurse, at_date=endOfPerviousMonth)

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

def update_mediawiki_page_mwclient(
    site_url: str,
    username: str,
    password: str,
    page_title: str,
    content: str,
    summary: str = "Updated via Python script",
    minor: bool = False,
) -> None:
    """
    Create or update a page on a MediaWiki site using mwclient.

    Args:
        site_url (str): Base URL of the wiki (e.g., "example.com").
        username (str): Wiki username.
        password (str): Wiki password.
        page_title (str): Title of the page to create/update.
        content (str): Page content.
        summary (str): Edit summary.
        minor (bool): Mark edit as minor.

    Returns:
        None
    """
    # Connect to the site
    site = mwclient.Site(site_url, path='/')
    site.login(username, password)

    # Get page object
    page = site.pages[page_title.replace(" ", "_")]

    # Save content (creates page if it doesn't exist)
    page.save(content, summary=summary, minor=minor)

    logger.info(f"Page '{page_title}' updated successfully on {site_url}!")


with open_book(bookPath, readonly=True) as book:
    overallAssetsAccount = book.accounts(fullname="Assets")
    currentAssetsAccount = book.accounts(fullname="Assets:Current Assets")
    otherAssetsAccount = book.accounts(fullname="Assets:Other Assets")
    incomeAccount = book.accounts(fullname="Income")
    expensesAccount = book.accounts(fullname="Expenses")
    imbalanceAccount = book.accounts(fullname="Imbalance-GBP")

    overallAssets = overallAssetsAccount.get_balance(at_date=endOfMonth)
    imbalance = imbalanceAccount.get_balance(at_date=endOfMonth)

    totalRevenue = delta(incomeAccount)
    totalExpenses = delta(expensesAccount)
    net = totalRevenue - totalExpenses

    currentAssets = {
        'name': currentAssetsAccount.name,
        'amount': formatGBP(currentAssetsAccount.get_balance(at_date=endOfMonth)),
        'children': {
            account.name: formatGBP(account.get_balance(at_date=endOfMonth))
            for account in currentAssetsAccount.children
        },
    }

    otherAssets = {
        'name': otherAssetsAccount.name,
        'children': {
            account.name: formatGBP(account.get_balance(at_date=endOfMonth))
            for account in otherAssetsAccount.children
        },
    }

    income = [
        {
            "name": account.name,
            "amount": formatGBP(delta(account, False)) if not account.placeholder else '',
            "children": child_deltas(account),
        }
        for account in incomeAccount.children
    ]

    expenses = [
        {
            "name": account.name,
            "amount": formatGBP(delta(account, False)) if not account.placeholder else '',
            "children": child_deltas(account),
        }
        for account in expensesAccount.children
    ]

    reportContent = template.render(
        month = f"{endOfMonth:%B}",
        year = f"{endOfMonth:%Y}",

        overallAssets = formatGBP(overallAssets),
        currentAssets = currentAssets,
        otherAssets = otherAssets,

        imbalance = formatGBP(imbalance),

        income = income,
        totalRevenue = formatGBP(totalRevenue),

        expenses = expenses,
        totalExpenses = formatGBP(totalExpenses),

        net = formatGBP(net),
        profitOrLoss = 'income' if net > 0 else 'loss',

        wikiYear = wikiYear,
    )

    reportName = f"Financials {endOfMonth:%Y-%m %B}"

    write_file(
        os.path.join(outputDir, f"{reportName}.md"),
        reportContent
    )

    update_mediawiki_page_mwclient(
        site_url=siteUrl,
        username=username,
        password=password,
        page_title=reportName,
        content=reportContent,
        summary="Automated Financials Report",
        minor=True,
    )

    # update last run date
    config['WikiReport']['last_run'] = datetime.now().isoformat()

    # and save it to the config
    with open(configFilename, 'w') as configfile:
        config.write(configfile)
