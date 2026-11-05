# tiger.price_extract

## 1.1.0
- Same ladder as 1.0.0, plus a price element it can point at: a sale mark, an item price, or the product's shown price. Tried before the loose amount next to the title.
- HTML-escaped JSON-LD is read. An amount may follow its currency sign (`12,50 €`).
- Text that tells a reader to ignore instructions is not treated as a price.
- A member-tier price shown in the hero (the "Now" amount) wins over a higher non-member price in the same offer.
- A price on another product (a recommendation or a product card) is not this product's price. When the product itself shows two amounts and neither is a sale price, no price is returned.
- A sale price and a higher price for the same buyer on one product is not a price. A member price is separate: the visible member amount stands, and the higher figure is the non-member price.
- Still no network and no model. 1.0.0 is unchanged.

## 1.0.0
- First version. Reads saved HTML only. JSON-LD Product, Offer, and AggregateOffer, then microdata, then Open Graph product meta, then a currency amount next to the title. A sale price wins over a list price. No price is returned unless the page shows it.
