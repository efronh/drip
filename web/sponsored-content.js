// SponsoredContent: the only place an ad is drawn.
//
// Rules it enforces by construction:
//  - always labelled "Sponsored", in its own landmark, outside the result area
//  - creative fields are set with textContent (never innerHTML), CTA must be https
//  - it has no access to the summary and nothing it does feeds back into processing
//  - hide() removes it; the app calls hide() as soon as processing ends
//
// Two surfaces: "status_line" renders inline next to the "Thinking..." text, so the status message
// itself carries the sponsor; "compact" / "card" render as a separate box under the status.

export class SponsoredContent {
  constructor(root, { onClick } = {}) {
    this.root = root;
    this.onClick = onClick;
    this.current = null;
    root.setAttribute("role", "complementary");
    root.setAttribute("aria-label", "Sponsored content");
    root.hidden = true;
  }

  show(ad, format = "compact") {
    if (!ad || typeof ad.cta_url !== "string" || !ad.cta_url.startsWith("https://")) return;
    const swap = this.current !== null;
    this.current = ad;
    const variant = format === "card" ? "card" : format === "status_line" ? "line" : "compact";
    this.root.className = `sponsored sponsored--${variant}`;
    this.root.replaceChildren(this.#render(ad, format));
    this.root.hidden = false;
    if (!swap) {
      this.root.classList.add("sponsored--enter");
      requestAnimationFrame(() => this.root.classList.remove("sponsored--enter"));
    }
  }

  hide() {
    if (this.root.hidden) return;
    this.current = null;
    this.root.classList.add("sponsored--leave");
    setTimeout(() => {
      if (this.current === null) {
        this.root.hidden = true;
        this.root.replaceChildren();
        this.root.classList.remove("sponsored--leave");
      }
    }, 180);
  }

  #render(ad, format) {
    const el = (tag, cls, text) => {
      const n = document.createElement(tag);
      if (cls) n.className = cls;
      if (text !== undefined) n.textContent = text;
      return n;
    };
    const link = (text) => {
      const cta = el("a", "sponsored__cta", text);
      cta.href = ad.cta_url;
      cta.target = "_blank";
      cta.rel = "sponsored noopener noreferrer";
      cta.addEventListener("click", () => this.onClick?.(ad));
      return cta;
    };
    if (format === "status_line") {
      // "Thinking...  ·  SPONSORED  Yemekçi — İlk siparişe 200 TL indirim  Detayları Gör ↗"
      const line = el("span", "sponsored__inner");
      line.append(el("span", "sponsored__label", "Sponsored"), el("span", "sponsored__advertiser", ad.advertiser),
                  el("span", "sponsored__headline", ad.headline), link(`${ad.cta_label} ↗`));
      return line;
    }
    const wrap = el("div", "sponsored__inner");
    const top = el("div", "sponsored__top");
    top.append(el("span", "sponsored__label", "Sponsored"), el("span", "sponsored__advertiser", ad.advertiser));
    wrap.append(top, el("div", "sponsored__headline", ad.headline));
    if (format === "card") wrap.append(el("div", "sponsored__body", ad.body));
    wrap.append(link(ad.cta_label));
    return wrap;
  }
}
