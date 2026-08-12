"""Deterministic landing-page renderer (LP-1).

`render_html(spec, *, page_id, track_url)` → a complete, mobile-first, tenant-branded HTML document.
**No LLM, no arbitrary HTML/JS:** every component is a fixed template; **all copy is HTML-escaped**.
The page carries a strict CSP (a per-render nonce for our own tiny track beacon only), `noindex` for
paid pages, and fires `landing_page.viewed` on load + a CTA event on interaction to `track_url`.

Craft notes (the page is the product on a paid-ad Persuade surface): a system **serif** display,
neutrals **hue-tinted from the brand** via `color-mix` (never flat gray), real offset+blur depth,
**authored SVG** icons (one consistent stroke — no emoji), a translucent **sticky mobile CTA**,
themed browser surfaces (selection / caret / focus / tabular numerals), and scroll-in reveals gated
behind `@supports(animation-timeline)` **and** `prefers-reduced-motion` so content is never hidden.
Fonts are self-contained system stacks (no external request under the page's own CSP); per-brand
webfont embedding is an LP-2 upgrade. The renderer is generic — nothing here names a vertical.
"""

from __future__ import annotations

import html
import json
import secrets
from typing import Any

from core.landing.spec import BrandTokens, Component, LandingPageSpec


def _esc(v: Any) -> str:
    return html.escape(str(v), quote=True)


# --- Authored icon set (one consistent stroke, currentColor — never emoji/glyphs) -----------------
_ICONS: dict[str, str] = {
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "spark": ('<path d="M12 3.2l1.9 5.6a3 3 0 0 0 1.9 1.9l5.6 1.9-5.6 1.9a3 3 0 0 0-1.9 1.9'
              'L12 22.2l-1.9-5.6a3 3 0 0 0-1.9-1.9L2.6 12.8l5.6-1.9a3 3 0 0 0 1.9-1.9z"/>'),
    "chat": ('<path d="M21 11.5a8 8 0 0 1-11.6 7.1L3.5 20l1.4-5.4A8 8 0 1 1 21 11.5z"/>'),
    "shield": '<path d="M12 3l7 3v5c0 4.4-3 7.6-7 9-4-1.4-7-4.6-7-9V6z"/><path d="M9 12l2 2 4-4"/>',
}


def _icon(name: str, cls: str = "lp-ic") -> str:
    body = _ICONS.get(name, "")
    return (f'<svg class="{cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" '
            f'aria-hidden="true">{body}</svg>')


# --- Components (each returns a fixed, escaped fragment) ------------------------------------------

def _hero(p: dict[str, Any]) -> str:
    img = (f'<div class="lp-hero-media"><img class="lp-hero-img" loading="eager" alt="" '
           f'src="{_esc(p["image_url"])}"></div>' if p.get("image_url") else "")
    sub = f'<p class="lp-sub">{_esc(p["subheadline"])}</p>' if p.get("subheadline") else ""
    orn = f'<div class="lp-orn" aria-hidden="true">{_icon("spark", "lp-ic lp-ic-accent")}</div>'
    return (f'<header class="lp-hero lp-reveal">{img}'
            f'<h1 class="lp-h1">{_esc(p["headline"])}</h1>{orn}{sub}</header>')


def _offer_banner(p: dict[str, Any]) -> str:
    return (f'<aside class="lp-banner lp-reveal">{_icon("spark", "lp-ic")}'
            f'<span>{_esc(p["text"])}</span></aside>')


def _product_grid(p: dict[str, Any]) -> str:
    cards = []
    for item in p.get("products", []):
        img = (f'<img loading="lazy" alt="" src="{_esc(item["image_url"])}">'
               if item.get("image_url") else '<div class="lp-noimg" aria-hidden="true"></div>')
        price_text = item.get("price_text", "")
        price = f'<span class="lp-price">{_esc(price_text)}</span>' if price_text else ""
        title = item.get("title", "")
        # interactive tile — one tap records which item the visitor liked (per-item analytics).
        return_attrs = (f'role="button" tabindex="0" data-lp-item="{_esc(item.get("ref", ""))}" '
                        f'data-lp-title="{_esc(title)}" data-lp-price="{_esc(price_text)}"')
        cards.append(
            f'<article class="lp-card" {return_attrs}><div class="lp-card-media">{img}</div>'
            f'<div class="lp-card-body"><h3>{_esc(title)}</h3>{price}'
            f'<span class="lp-card-cue">Enquire →</span></div></article>')
    return f'<section class="lp-grid lp-reveal">{"".join(cards)}</section>'


def _trust_bar(p: dict[str, Any]) -> str:
    items = "".join(
        f'<li>{_icon("check", "lp-ic lp-ic-accent")}<span>{_esc(i)}</span></li>'
        for i in p.get("items", []))
    return f'<ul class="lp-trust lp-reveal">{items}</ul>'


def _benefits(p: dict[str, Any]) -> str:
    rows = "".join(
        f'<li class="lp-benefit"><span class="lp-bchip">{_icon("spark")}</span>'
        f'<div><h4>{_esc(i.get("title", ""))}</h4>'
        f'<p>{_esc(i.get("detail", ""))}</p></div></li>' for i in p.get("items", []))
    return f'<ul class="lp-benefits lp-reveal">{rows}</ul>'


def _testimonials(p: dict[str, Any]) -> str:
    items = "".join(
        f'<figure class="lp-quote"><blockquote>{_esc(i.get("quote", ""))}</blockquote>'
        f'<figcaption>{_esc(i.get("author", ""))}</figcaption></figure>'
        for i in p.get("items", []))
    return f'<section class="lp-quotes lp-reveal">{items}</section>'


def _faq(p: dict[str, Any]) -> str:
    items = "".join(
        f'<details class="lp-faq"><summary>{_esc(i.get("q", ""))}<span class="lp-chev" '
        f'aria-hidden="true"></span></summary><p>{_esc(i.get("a", ""))}</p></details>'
        for i in p.get("items", []))
    return f'<section class="lp-faqs lp-reveal">{items}</section>'


def _whatsapp_cta(p: dict[str, Any]) -> str:
    note = f'<p class="lp-cta-note">{_esc(p["note"])}</p>' if p.get("note") else ""
    label = _esc(p["label"])
    return (f'<section class="lp-cta lp-reveal">{note}'
            f'<button type="button" class="lp-btn" data-lp-cta="whatsapp">'
            f'{_icon("chat", "lp-ic")}<span>{label}</span></button></section>')


def _lead_form(p: dict[str, Any]) -> str:
    fields = p.get("fields") or [
        {"name": "name", "label": "Your name", "type": "text"},
        {"name": "phone", "label": "WhatsApp number", "type": "tel"}]
    rows = "".join(
        f'<label>{_esc(fld.get("label", fld.get("name", "")))}'
        f'<input name="{_esc(fld.get("name", ""))}" '
        f'type="{_esc(fld.get("type", "text"))}" required></label>'
        for fld in fields)
    consent = _esc(p.get("consent_text", "I agree to be contacted about this enquiry."))
    return (f'<form class="lp-form lp-reveal" data-lp-cta="lead_form">{rows}'
            f'<label class="lp-consent"><input type="checkbox" required>'
            f'<span>{consent}</span></label>'
            f'<button type="submit" class="lp-btn">{_esc(p["submit_label"])}</button></form>')


def _footer(p: dict[str, Any]) -> str:
    return f'<footer class="lp-footer">{_esc(p["text"])}</footer>'


_RENDERERS = {
    "hero": _hero, "offer_banner": _offer_banner, "product_grid": _product_grid,
    "trust_bar": _trust_bar, "benefits": _benefits, "testimonials": _testimonials,
    "faq": _faq, "whatsapp_cta": _whatsapp_cta, "lead_form": _lead_form, "footer": _footer,
}


def _render_component(c: Component) -> str:
    fn = _RENDERERS.get(c.type)
    return fn(c.props) if fn else ""  # unknown types are dropped (validation already rejected them)


def _sticky_cta(label: str) -> str:
    return (f'<div class="lp-sticky"><button type="button" class="lp-btn lp-btn-sm" '
            f'data-lp-cta="whatsapp">{_icon("chat", "lp-ic")}<span>{_esc(label)}</span>'
            f'</button></div>')


# Static page CSS (no interpolation → plain string; brand values come from the :root vars in _css).
_STATIC_CSS = (
    # neutrals + surfaces derived from the brand hue (never flat gray); gray is only a fallback.
    ":root{--muted:#6b6b6b;--muted:color-mix(in srgb,var(--text) 56%,var(--bg));"
    "--faint:#8c8c8c;--faint:color-mix(in srgb,var(--text) 40%,var(--bg));"
    "--line:#e7e2da;--line:color-mix(in srgb,var(--ink) 12%,transparent);"
    "--surface:#ffffff;--surface:color-mix(in srgb,var(--bg) 45%,#fff);"
    "--tint:color-mix(in srgb,var(--accent) 10%,var(--bg));"
    "--sh-1:0 1px 2px rgb(0 0 0/.05),0 1px 1px rgb(0 0 0/.04);"
    "--sh-2:0 6px 20px -8px color-mix(in srgb,var(--ink) 45%,transparent),0 2px 6px rgb(0 0 0/.05);"
    "--sh-3:0 22px 48px -18px color-mix(in srgb,var(--ink) 55%,transparent);"
    "--r:14px;--r-lg:22px;--r-pill:999px;--maxw:680px;color-scheme:light}"
    "*{box-sizing:border-box}"
    "html{-webkit-text-size-adjust:100%;caret-color:var(--accent);"
    "scrollbar-color:color-mix(in srgb,var(--ink) 30%,var(--bg)) transparent}"
    "body{margin:0;font-family:var(--bf);color:var(--text);"
    "background:radial-gradient(125% 80% at 50% -12%,"
    "color-mix(in srgb,var(--accent) 15%,var(--bg)),var(--bg) 56%) no-repeat,var(--bg);"
    "line-height:1.55;-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;"
    "-webkit-tap-highlight-color:transparent}"
    "img{max-width:100%;display:block}"
    "::selection{background:color-mix(in srgb,var(--accent) 22%,var(--bg));color:var(--ink)}"
    ":focus-visible{outline:2px solid var(--accent);outline-offset:3px;border-radius:6px}"
    "h1,h2,h3,h4{font-family:var(--hf);color:var(--ink);font-weight:600;"
    "letter-spacing:-.01em;text-wrap:balance;margin:0}"
    "p{margin:0}"
    ".lp-wrap{max-width:var(--maxw);margin:0 auto;padding:0 20px 40px}"
    # masthead wordmark
    ".lp-mast{text-align:center;font-family:var(--hf);font-weight:600;color:var(--ink);"
    "font-size:1.02rem;letter-spacing:.16em;text-transform:uppercase;padding:26px 0 4px}"
    ".lp-mast::after{content:'';display:block;width:34px;height:2px;margin:11px auto 0;"
    "border-radius:2px;background:var(--accent)}"
    # hero
    ".lp-hero{text-align:center;padding:26px 4px 6px}"
    ".lp-hero-media{margin:0 0 24px}"
    ".lp-hero-img{width:100%;aspect-ratio:16/10;object-fit:cover;border-radius:var(--r-lg);"
    "box-shadow:var(--sh-3)}"
    ".lp-h1{font-size:clamp(2rem,7.4vw,3.1rem);line-height:1.04;letter-spacing:-.032em;"
    "margin:0 auto;max-width:14ch}"
    ".lp-orn{display:flex;align-items:center;justify-content:center;gap:12px;margin:16px auto 0;"
    "max-width:150px;color:var(--accent)}"
    ".lp-orn::before,.lp-orn::after{content:'';height:1px;flex:1;background:linear-gradient("
    "90deg,transparent,color-mix(in srgb,var(--accent) 55%,transparent))}"
    ".lp-orn::after{background:linear-gradient("
    "90deg,color-mix(in srgb,var(--accent) 55%,transparent),transparent)}"
    ".lp-orn .lp-ic{width:15px;height:15px}"
    ".lp-sub{color:var(--muted);font-size:clamp(1.02rem,3.6vw,1.2rem);margin:16px auto 0;"
    "max-width:34ch}"
    # offer ribbon (the one loud element — filled accent)
    ".lp-banner{display:flex;gap:9px;align-items:center;justify-content:center;text-align:center;"
    "background:var(--accent);color:#fff;font-weight:600;letter-spacing:.01em;"
    "padding:13px 20px;border-radius:var(--r-pill);margin:28px auto 0;width:fit-content;"
    "max-width:100%;box-shadow:var(--sh-2)}"
    ".lp-banner .lp-ic{width:17px;height:17px}"
    # product grid
    ".lp-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:30px 0 0}"
    ".lp-card{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);"
    "overflow:hidden;box-shadow:var(--sh-1);transition:transform .35s cubic-bezier(.22,1,.36,1),"
    "box-shadow .35s cubic-bezier(.22,1,.36,1)}"
    ".lp-card-media{aspect-ratio:1/1;overflow:hidden}"
    ".lp-card-media img,.lp-noimg{width:100%;height:100%;object-fit:cover}"
    ".lp-noimg{background:radial-gradient(120% 120% at 30% 20%,"
    "color-mix(in srgb,var(--accent) 14%,var(--surface)),var(--surface))}"
    ".lp-card[data-lp-item]{cursor:pointer}"
    ".lp-card-body{padding:12px 14px 15px}"
    ".lp-card h3{font-size:.98rem;font-weight:600;line-height:1.25}"
    ".lp-price{display:block;margin-top:6px;color:var(--accent);font-weight:700;"
    "font-variant-numeric:tabular-nums;letter-spacing:-.01em}"
    ".lp-card-cue{display:block;margin-top:8px;font-size:.78rem;font-weight:600;"
    "letter-spacing:.03em;color:var(--faint);transition:color .2s ease}"
    "@media(hover:hover){.lp-card:hover{transform:translateY(-3px);box-shadow:var(--sh-2)}"
    ".lp-card:hover .lp-card-cue{color:var(--accent)}}"
    # trust (an accent-tinted band)
    ".lp-trust{list-style:none;display:flex;flex-wrap:wrap;gap:8px 18px;justify-content:center;"
    "margin:30px 0 0;padding:18px 20px;background:var(--tint);border-radius:var(--r-lg)}"
    ".lp-trust li{display:inline-flex;align-items:center;gap:8px;font-size:.88rem;"
    "font-weight:600;color:var(--ink)}"
    # benefits (a refined list with accent icon-chips, not boxed cards)
    ".lp-benefits{list-style:none;padding:0;margin:38px 0 0;display:grid;gap:2px}"
    ".lp-benefit{display:flex;gap:15px;align-items:flex-start;padding:18px 4px;"
    "border-top:1px solid var(--line)}"
    ".lp-benefit:first-child{border-top:0}"
    ".lp-bchip{display:grid;place-items:center;width:40px;height:40px;flex:none;border-radius:12px;"
    "background:var(--tint);color:var(--accent)}"
    ".lp-bchip .lp-ic{width:20px;height:20px}"
    ".lp-benefit h4{font-size:1.06rem}"
    ".lp-benefit p{color:var(--muted);margin-top:3px;font-size:.96rem}"
    # testimonials (on a tinted surface)
    ".lp-quotes{display:grid;gap:20px;margin:40px 0 0;padding:26px 22px;"
    "background:color-mix(in srgb,var(--ink) 5%,var(--surface));border-radius:var(--r-lg)}"
    ".lp-quote{margin:0;padding:0}"
    ".lp-quote blockquote{margin:0;font-family:var(--hf);font-size:1.18rem;line-height:1.4;"
    "letter-spacing:-.01em;color:var(--ink)}"
    ".lp-quote blockquote::before{content:open-quote;color:var(--accent);"
    "font-size:1.4em;line-height:0;margin-right:.05em;vertical-align:-.35em}"
    ".lp-quote figcaption{margin-top:9px;color:var(--faint);font-size:.85rem;"
    "text-transform:uppercase;letter-spacing:.08em}"
    # faq
    ".lp-faqs{margin:40px 0 0;border-top:1px solid var(--line)}"
    ".lp-faq{border-bottom:1px solid var(--line)}"
    ".lp-faq summary{display:flex;align-items:center;justify-content:space-between;gap:12px;"
    "list-style:none;cursor:pointer;padding:16px 2px;font-weight:600;color:var(--ink);"
    "font-size:1.02rem}"
    ".lp-faq summary::-webkit-details-marker{display:none}"
    ".lp-faq p{color:var(--muted);padding:0 2px 16px;margin-top:-4px;font-size:.97rem}"
    ".lp-chev{width:11px;height:11px;border-right:2px solid var(--faint);"
    "border-bottom:2px solid var(--faint);transform:rotate(45deg);flex:none;"
    "transition:transform .28s cubic-bezier(.22,1,.36,1)}"
    ".lp-faq[open] .lp-chev{transform:rotate(225deg)}"
    # CTA (a deep-accent destination panel — the one decisive move)
    ".lp-cta{text-align:center;margin:48px 0 0;padding:38px 24px;border-radius:var(--r-lg);"
    "background:linear-gradient(160deg,var(--accent),"
    "color-mix(in srgb,var(--accent) 78%,#000));box-shadow:var(--sh-3)}"
    ".lp-btn{display:inline-flex;align-items:center;justify-content:center;gap:10px;width:100%;"
    "max-width:440px;background:var(--accent);color:#fff;border:0;border-radius:var(--r);"
    "padding:17px 22px;font:600 1.06rem/1 var(--bf);cursor:pointer;box-shadow:var(--sh-2);"
    "transition:transform .2s cubic-bezier(.22,1,.36,1),box-shadow .2s cubic-bezier(.22,1,.36,1)}"
    # inverse button inside the accent panel (white on accent)
    ".lp-cta .lp-btn{background:#fff;color:var(--accent);"
    "box-shadow:0 10px 26px -10px rgb(0 0 0/.5)}"
    "@media(hover:hover){.lp-btn:hover{transform:translateY(-2px);box-shadow:var(--sh-3)}}"
    ".lp-btn:active{transform:translateY(0) scale(.99)}"
    ".lp-btn .lp-ic{width:21px;height:21px;stroke-width:1.9}"
    ".lp-cta-note{color:#fff;font-family:var(--hf);font-size:1.32rem;font-weight:600;"
    "letter-spacing:-.01em;margin:0 auto 18px;max-width:22ch}"
    # lead form
    ".lp-form{display:grid;gap:14px;background:var(--surface);border:1px solid var(--line);"
    "padding:20px;border-radius:var(--r-lg);margin:38px 0 0;box-shadow:var(--sh-1)}"
    ".lp-form label{display:grid;gap:6px;font-size:.82rem;font-weight:600;color:var(--ink);"
    "text-transform:uppercase;letter-spacing:.05em}"
    ".lp-form input[type=text],.lp-form input[type=tel]{padding:13px;border:1px solid var(--line);"
    "border-radius:11px;font-size:16px;font-family:var(--bf);background:var(--bg);"
    "color:var(--text)}"
    ".lp-form input:focus{outline:2px solid var(--accent);outline-offset:1px;"
    "border-color:transparent}"
    ".lp-consent{flex-direction:row;grid-auto-flow:column;justify-content:start;align-items:center;"
    "gap:9px;font-weight:400;text-transform:none;letter-spacing:normal;color:var(--muted);"
    "font-size:.86rem}"
    ".lp-consent input{accent-color:var(--accent);width:18px;height:18px}"
    # icons + footer
    ".lp-ic{width:18px;height:18px;flex:none}.lp-ic-accent{color:var(--accent)}"
    ".lp-footer{text-align:center;color:var(--faint);font-size:.8rem;padding:44px 0 0;"
    "letter-spacing:.02em}"
    # sticky mobile CTA (translucent material over safe-area)
    ".lp-sticky{position:fixed;left:0;right:0;bottom:0;z-index:20;padding:10px 16px;"
    "padding-bottom:calc(10px + env(safe-area-inset-bottom));"
    "background:color-mix(in srgb,var(--bg) 78%,transparent);"
    "backdrop-filter:blur(16px) saturate(160%);-webkit-backdrop-filter:blur(16px) saturate(160%);"
    "border-top:1px solid var(--line)}"
    ".lp-sticky .lp-btn{max-width:none;padding:14px 20px;box-shadow:none}"
    ".lp-btn-sm{font-size:1rem}"
    "body{padding-bottom:86px}"
    "@media(min-width:560px){.lp-sticky{display:none}body{padding-bottom:0}"
    ".lp-grid{grid-template-columns:1fr 1fr 1fr}}"
    # motion — one authored moment + scroll reveals, gated so content is never hidden
    "@media(prefers-reduced-motion:no-preference){"
    ".lp-hero .lp-h1{animation:lp-up .7s cubic-bezier(.16,1,.3,1) both}"
    ".lp-hero .lp-sub{animation:lp-up .7s cubic-bezier(.16,1,.3,1) .06s both}"
    "@supports(animation-timeline:view()){"
    ".lp-reveal:not(.lp-hero){opacity:.001;translate:0 16px;"
    "animation:lp-in linear both;animation-timeline:view();animation-range:entry 2% cover 20%}}}"
    "@keyframes lp-up{from{opacity:.001;translate:0 14px}to{opacity:1;translate:0 0}}"
    "@keyframes lp-in{to{opacity:1;translate:0 0}}"
)


def _css(b: BrandTokens) -> str:
    root = (
        f":root{{--ink:{_esc(b.primary)};--accent:{_esc(b.accent)};--bg:{_esc(b.background)};"
        f"--text:{_esc(b.text)};--hf:{_esc(b.heading_font)};--bf:{_esc(b.body_font)}}}"
    )
    return root + _STATIC_CSS


def _beacon_js(page_id: str, track_url: str, variant: str, wa_number: str) -> str:
    """First-party capture beacon (own beacon only, CSP nonce-gated). Records page + **per-item**
    engagement with a rich, first-party context bundle (utm, referrer, device, scroll, dwell) so the
    store learns which items are most wanted. When a WhatsApp number is set, a tap deep-links to
    WhatsApp **prefilled with the item** — item intent + a real contact from a single click."""
    cfg = json.dumps({"page_id": str(page_id), "url": str(track_url),
                      "variant": str(variant), "wa": str(wa_number)})
    return (
        "var LP=" + cfg + ";var LPs=0,LPt=Date.now(),LPi=null,LPl='';"
        "try{LPs=sessionStorage;var k='lp_sid';LPs=LPs.getItem(k)||"
        "(Math.random().toString(36).slice(2)+Date.now().toString(36));"
        "sessionStorage.setItem(k,LPs);}catch(e){LPs=''}"
        "var LPmax=0;function lpDev(){var w=innerWidth;"
        "return w<560?'mobile':(w<960?'tablet':'desktop');}"
        "function lpUtm(){var q=new URLSearchParams(location.search),o={},"
        "n=['source','medium','campaign','term','content'];"
        "n.forEach(function(x){var v=q.get('utm_'+x);if(v)o[x]=v.slice(0,120);});return o;}"
        "function lpRef(){try{return document.referrer?new URL(document.referrer).host:'';}"
        "catch(e){return ''}}"
        "function lpTrack(t,item,sec){try{var b={page_id:LP.page_id,type:t,session_id:LPs,"
        "variant:LP.variant,utm:lpUtm(),item_ref:item||null,"
        "meta:{section:sec||null,device:lpDev(),referrer:lpRef(),"
        "scroll:LPmax,dwell:Math.round((Date.now()-LPt)/1000)}};"
        "navigator.sendBeacon(LP.url,new Blob([JSON.stringify(b)],"
        "{type:'application/json'}));}catch(e){}}"
        "function lpWa(item,label){if(!LP.wa)return;try{var msg=label?"
        "('Hi, I'+String.fromCharCode(39)+'m interested in '+label):"
        "'Hi, I have an enquiry';location.assign('https://wa.me/'+LP.wa+"
        "'?text='+encodeURIComponent(msg));}catch(e){}}"
        "addEventListener('scroll',function(){var h=document.documentElement,"
        "p=Math.round((h.scrollTop+innerHeight)/h.scrollHeight*100);"
        "if(p>LPmax)LPmax=Math.min(100,p);},{passive:true});"
        "try{var io=new IntersectionObserver(function(es){es.forEach(function(en){"
        "if(en.isIntersecting){var el=en.target,r=el.dataset.lpItem;"
        "if(r&&!el.dataset.lpSeen){el.dataset.lpSeen='1';"
        "lpTrack('landing_page.item_viewed',r,'product_grid');}}});},{threshold:.5});"
        "document.querySelectorAll('[data-lp-item]').forEach(function(el){io.observe(el);});"
        "}catch(e){}"
        "function lpItem(el){LPi=el.dataset.lpItem;LPl=(el.dataset.lpTitle||'')+"
        "(el.dataset.lpPrice?(' ('+el.dataset.lpPrice+')'):'');"
        "lpTrack('landing_page.item_clicked',LPi,'product_grid');lpWa(LPi,LPl);}"
        "document.addEventListener('click',function(e){"
        "var it=e.target.closest('[data-lp-item]');"
        "if(it){lpItem(it);return;}"
        "var el=e.target.closest('[data-lp-cta]');"
        "if(el&&el.dataset.lpCta==='whatsapp'){"
        "lpTrack('landing_page.cta_clicked',LPi,'cta');lpWa(LPi,LPl);}});"
        "document.addEventListener('keydown',function(e){"
        "if((e.key==='Enter'||e.key===' ')){var it=e.target.closest('[data-lp-item]');"
        "if(it){e.preventDefault();lpItem(it);}}});"
        "document.addEventListener('submit',function(e){var f=e.target.closest('[data-lp-cta]');"
        "if(f){e.preventDefault();lpTrack('landing_page.form_submitted',LPi,'lead_form');}});"
        "lpTrack('landing_page.viewed',null,null);"
    )


def _cta_props(spec: LandingPageSpec) -> dict[str, Any]:
    """The whatsapp CTA's props (label + optional wa_number), if the page has one."""
    for c in spec.sections:
        if c.type == "whatsapp_cta":
            return c.props
    return {}


def render_html(
    spec: LandingPageSpec, *, page_id: str, track_url: str = "", variant: str = "default"
) -> str:
    nonce = secrets.token_urlsafe(12)
    mast = f'<div class="lp-mast">{_esc(spec.brand.name)}</div>'
    body = mast + "".join(_render_component(c) for c in spec.sections)
    cta = _cta_props(spec)
    cta_label = str(cta.get("label", "")) if cta else ""
    wa_number = "".join(ch for ch in str(cta.get("wa_number", "")) if ch.isdigit())
    sticky = _sticky_cta(cta_label) if cta_label else ""
    robots = '<meta name="robots" content="noindex,nofollow">' if spec.noindex else ""
    csp = ("default-src 'none'; img-src 'self' https: data:; style-src 'unsafe-inline'; "
           f"connect-src 'self' https:; script-src 'nonce-{nonce}'; base-uri 'none'; "
           "form-action 'self'")
    beacon = (f'<script nonce="{nonce}">'
              f'{_beacon_js(page_id, track_url, variant, wa_number)}</script>'
              if track_url else "")
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        f'<meta http-equiv="Content-Security-Policy" content="{_esc(csp)}">{robots}'
        f'<meta name="theme-color" content="{_esc(spec.brand.accent)}">'
        f"<title>{_esc(spec.title)}</title>"
        f'<meta name="description" content="{_esc(spec.meta_description)}">'
        f"<style>{_css(spec.brand)}</style></head>"
        f'<body><main class="lp-wrap">{body}</main>{sticky}{beacon}</body></html>'
    )
