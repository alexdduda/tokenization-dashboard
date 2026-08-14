/**
 * Static regulatory context.
 *
 * Kept as prose in the component rather than in the data payload: it is an editorial
 * argument about the market, not a measurement, and it should not look like something
 * the pipeline derived.
 */
export function ContextPanel() {
  return (
    <section className="card">
      <h2>Why the GENIUS Act matters to this chart</h2>
      <div className="prose">
        <p>
          The GENIUS Act established a federal framework for payment stablecoins,
          requiring issuers to hold full reserves in cash and short-dated US Treasuries
          and to report on those holdings. Its relevance to tokenized Treasuries is
          structural rather than incidental: it turned a reserve practice that issuers
          had adopted voluntarily into a statutory obligation, and in doing so made
          short-dated Treasuries the mandated backing asset for regulated dollar
          stablecoins.
        </p>

        <h3>The demand channel</h3>
        <p>
          A stablecoin issuer that must hold T-bills has two ways to do it: through
          conventional custody, or by holding a tokenized Treasury fund that settles on
          the same rails as the token it backs. The second is what the products on this
          page provide. Regulatory certainty about the reserve requirement therefore
          feeds demand for tokenized Treasuries directly, which is part of why this
          market roughly tripled across the period charted above.
        </p>

        <h3>The infrastructure argument</h3>
        <p>
          The more consequential effect is on plumbing. Once reserves are tokenized, an
          issuer can move backing assets and redeem around the clock instead of within
          banking hours, and the reserve attestation becomes a query against a chain
          rather than a monthly PDF. That is the actual case for tokenization here — not
          yield, which is identical to the underlying bill, but settlement speed,
          composability and transparency of the reserve.
        </p>

        <h3>What it does not settle</h3>
        <p>
          The Act governs payment stablecoins, not tokenized securities. The funds on
          this page remain regulated as what they already were: BENJI as a 1940 Act
          money market fund, BUIDL and OUSG under Reg D exemptions, USDY as a secured
          note. That fragmentation is why the products differ so much in who may hold
          them, and why the table above distinguishes retail from accredited access.
        </p>
      </div>
    </section>
  );
}
