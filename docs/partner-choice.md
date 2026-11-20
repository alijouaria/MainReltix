# Partner choice: why an energy supplier, why the expiring contract

## The partner: an energy supplier

- **Every tenant needs one.** Electricity applies to every unit in the portfolio. Repairs or insurance add-ons apply only to some. It is the broadest demand centrix can serve from day one.
- **It recurs.** Supply contracts end and renew every one to two years, and again at every move-in and move-out. One integration creates demand again and again, which is the marketplace effect reltix describes.
- **centrix already holds what the supplier needs.** That is address, meter number, move-in date and a meter reading. Today tenants type these into every supplier's form. Here they arrive pre-filled.
- **The value is concrete for the tenant.** Many households stay on expensive default or auto-renewed tariffs because switching takes forms, research and remembering a deadline. Taking that friction away is the product. In the sample unit, the tenant pays €82.50/month today; the partner offers €78.40 (100% renewable) or €74.90, which saves about €49–91 a year with zero forms.
- **Low risk for the landlord and for reltix.** A tenant's energy contract is the tenant's own, so the landlord only needs a summary. reltix only has to facilitate it, with no liability for the supply itself.

## The moment: the contract is about to expire

- **A precise, predictable trigger.** The end date is known weeks in advance. centrix opens the case `renewal_notice_days` (30) before `expiry_date`, so it acts early and calmly, without chasing anyone at the last minute.
- **Missing it costs money.** An expired contract usually rolls into a more expensive default or renewal tariff. Acting before the date is a clear, measurable benefit.
- **The loop closes naturally.** A switch needs the meter reading on the switch date anyway. centrix collects it once (remote read, one photo or one quick entry), passes it to the supplier and files the signed contract on the unit.
- **The same engine covers the neighbouring moments.** Move-in and move-out also require a new contract, so the flow extends to them without redesign.

## Minimal friction for all three parties

| Party | What they do |
|---|---|
| Tenant | Shares their contract once (at move-in or with one bill photo), then confirms the new offer with one tap |
| Landlord | Nothing to do; gets a one-line summary when the new contract is active |
| reltix | Nothing manual; centrix detects the moment, pre-fills the order, tracks status and files the result |
| Partner | Receives a complete, consented order through the [connector](connector-spec.md) and reports status back |

## Assumption we state openly

Property managers do not normally see a tenant's private energy contract. We assume the tenant opts in once, so centrix learns the supplier, end date and usage. Without that opt-in, centrix still offers the switch at move-in and move-out, when a new contract is needed anyway.
