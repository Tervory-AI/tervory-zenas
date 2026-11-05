# tiger.price_extract

## 1.0.0
- First version. Reads saved HTML only. JSON-LD Product, Offer, and AggregateOffer, then microdata, then Open Graph product meta, then a currency amount next to the title. A sale price wins over a list price. No price is returned unless the page shows it.
