# gnucash-imports
Import scripts for Nottingham Hackspace GNUCash

## Requirments
Python3 libaries

```
piecash
stripe
requests
requests_oauthlib
python-dateutil
```

Config file
copy `imports.example.cfg` to `imports.cfg` and add your api keys

## GnuCash
Your book must be saved in a sqlite format (not XML) for pie cash to access it

It is also assumed the following accounts exist in your book
```
Assets:Current Assets:TSB Account
Assets:Current Assets:Stripe
Assets:Current Assets:SumUp
Expenses:Bank Service Charge
Expenses:Miscellaneous
Income:Snackspace
Income:Donations
```

## Stripe
Just add you secret api key

## SumUp
You need to use something like Postman to get a inital `access` and `refresh` tokens

## TSB
This is expected to be called from TSBScrape
