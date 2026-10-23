"""
reasons.py - turns the model's top SHAP contributions into plain-language warnings.

Several raw features can describe the same human reason (e.g. amt_over_median and
amt_over_max both mean "unusually large"), so contributions are grouped by REASON
before picking the top 3. Texts are in English (en), Tamil (ta) and Hindi (hi).
NOTE: have a native speaker review the ta/hi strings before the demo.
"""
import math

REASONS = {
    "AMOUNT": {
        "en": "The amount is much higher than you usually pay.",
        "ta": "நீங்கள் வழக்கமாகச் செலுத்துவதை விட இந்தத் தொகை மிகவும் அதிகம்.",
        "hi": "यह राशि आपके सामान्य भुगतान से काफ़ी ज़्यादा है।",
    },
    "NEW_PAYEE": {
        "en": "You have never paid this person before.",
        "ta": "இந்த நபருக்கு நீங்கள் இதுவரை பணம் செலுத்தியதில்லை.",
        "hi": "आपने पहले कभी इस व्यक्ति को भुगतान नहीं किया है।",
    },
    "NEW_DEVICE": {
        "en": "This payment is coming from a device we have not seen on your account.",
        "ta": "உங்கள் கணக்கில் இதுவரை பார்க்காத சாதனத்திலிருந்து இந்தப் பணம் அனுப்பப்படுகிறது.",
        "hi": "यह भुगतान ऐसे डिवाइस से हो रहा है जो आपके खाते पर पहले नहीं देखा गया।",
    },
    "LOCATION": {
        "en": "This payment is coming from an unusual location for you.",
        "ta": "இந்தப் பணப்பரிவர்த்தனை உங்களுக்குப் பழக்கமில்லாத இடத்திலிருந்து நடக்கிறது.",
        "hi": "यह भुगतान आपके लिए असामान्य जगह से हो रहा है।",
    },
    "TIME": {
        "en": "This is an unusual time of day for you to make a payment.",
        "ta": "நீங்கள் பணம் செலுத்தும் வழக்கமான நேரம் இதுவல்ல.",
        "hi": "आप आमतौर पर इस समय भुगतान नहीं करते।",
    },
    "BURST": {
        "en": "You are sending several payments to new people in a short time.",
        "ta": "குறுகிய நேரத்தில் புதியவர்களுக்குப் பல பணப்பரிவர்த்தனைகள் செய்கிறீர்கள்.",
        "hi": "आप थोड़े समय में नए लोगों को कई भुगतान कर रहे हैं।",
    },
    "COLLECT": {
        "en": "This is a collect request. Approving it sends money OUT of your account. "
              "Banks never ask you to approve a request to receive money.",
        "ta": "இது ஒரு 'கலெக்ட்' கோரிக்கை. இதை ஏற்றால் உங்கள் கணக்கிலிருந்து பணம் செல்லும். "
              "பணம் பெறுவதற்காக அனுமதி கேட்கும்படி வங்கிகள் ஒருபோதும் கூறாது.",
        "hi": "यह एक कलेक्ट रिक्वेस्ट है। इसे मंज़ूर करने पर आपके खाते से पैसे कट जाएंगे। "
              "बैंक कभी पैसे पाने के लिए रिक्वेस्ट मंज़ूर करने को नहीं कहते।",
    },
    "CALL": {
        "en": "You seem to be on a phone call. Scammers pretend to be bank or government officials. "
              "Never pay because someone on a call tells you to.",
        "ta": "நீங்கள் தொலைபேசி அழைப்பில் இருப்பது போல் தெரிகிறது. மோசடிக்காரர்கள் வங்கி அல்லது அரசு அதிகாரிகள் போல நடிப்பார்கள். "
              "அழைப்பில் யாராவது சொல்வதற்காகப் பணம் செலுத்தாதீர்கள்.",
        "hi": "लगता है आप फ़ोन कॉल पर हैं। ठग बैंक या सरकारी अधिकारी बनकर बात करते हैं। "
              "कॉल पर किसी के कहने पर भुगतान न करें।",
    },
    "SCREEN": {
        "en": "A screen-sharing or remote-access app seems to be active. Someone may be watching your phone. "
              "Stop and close it.",
        "ta": "திரைப் பகிர்வு அல்லது ரிமோட் ஆப் செயலில் இருப்பது போல் தெரிகிறது. யாரோ உங்கள் தொலைபேசியைப் பார்க்கக்கூடும். "
              "நிறுத்தி அதை மூடுங்கள்.",
        "hi": "स्क्रीन-शेयरिंग या रिमोट-एक्सेस ऐप चालू लगता है। कोई आपका फ़ोन देख रहा हो सकता है। इसे रोकें और बंद करें।",
    },
    "QR": {
        "en": "This QR code now pays a different account from this shop's usual account.",
        "ta": "இந்த QR குறியீடு, இந்தக் கடையின் வழக்கமான கணக்குக்கு அல்லாமல் வேறு கணக்குக்குப் பணம் அனுப்புகிறது.",
        "hi": "यह QR कोड अब इस दुकान के सामान्य खाते की जगह किसी दूसरे खाते में पैसे भेजता है।",
    },
    "NEW_ACCOUNT": {
        "en": "The account receiving the money is very new.",
        "ta": "பணம் பெறும் கணக்கு மிகவும் புதியது.",
        "hi": "पैसे पाने वाला खाता बहुत नया है।",
    },
    "MULE_PATTERN": {
        "en": "The receiving account is collecting money from many people and passing it on quickly, "
              "a common sign of fraud.",
        "ta": "பணம் பெறும் கணக்கு பலரிடமிருந்து பணம் பெற்று உடனே அனுப்புகிறது; இது மோசடியின் பொதுவான அறிகுறி.",
        "hi": "पैसे पाने वाला खाता कई लोगों से पैसे लेकर तुरंत आगे भेज रहा है, यह धोखाधड़ी का आम संकेत है।",
    },
    "KNOWN_MULE": {
        "en": "This account is linked to accounts already reported for fraud.",
        "ta": "இந்தக் கணக்கு ஏற்கனவே மோசடிக்காகப் புகாரளிக்கப்பட்ட கணக்குகளுடன் தொடர்புடையது.",
        "hi": "यह खाता पहले से धोखाधड़ी के लिए रिपोर्ट किए गए खातों से जुड़ा है।",
    },
    # analyst-only (the sender is itself a suspected mule): shown in English in the dashboard
    "PASSTHROUGH": {
        "en": "Sender account received money moments ago and is forwarding it on (pass-through).",
        "ta": "Sender account received money moments ago and is forwarding it on (pass-through).",
        "hi": "Sender account received money moments ago and is forwarding it on (pass-through).",
    },
}

FEATURE_TO_REASON = {
    "amt_over_median": "AMOUNT", "amt_over_max": "AMOUNT", "log_amount": "AMOUNT",
    "is_new_payee": "NEW_PAYEE", "payee_txn_count": "NEW_PAYEE",
    "is_new_device": "NEW_DEVICE", "dist_km": "LOCATION", "hour_dev": "TIME", "hour": "TIME",
    "vel_1h": "BURST", "vel_24h": "BURST", "amt_sum_1h": "BURST", "new_payees_1h": "BURST",
    "is_collect": "COLLECT", "call_active": "CALL", "screen_share": "SCREEN", "qr_mismatch": "QR",
    "recv_age_days": "NEW_ACCOUNT", "recv_in_total": "NEW_ACCOUNT",
    "recv_in_1h": "MULE_PATTERN", "recv_in_24h": "MULE_PATTERN", "recv_out_24h": "MULE_PATTERN",
    "recv_uniq_senders_24h": "MULE_PATTERN", "recv_in_out_ratio": "MULE_PATTERN",
    "recv_pt_min_24h": "MULE_PATTERN", "recv_pagerank": "MULE_PATTERN", "recv_comp_size": "MULE_PATTERN",
    "recv_hops_seed": "KNOWN_MULE", "recv_is_seed": "KNOWN_MULE",
    "send_in_24h": "PASSTHROUGH", "send_in_amt_24h": "PASSTHROUGH", "send_since_last_in": "PASSTHROUGH",
    "send_flowthrough": "PASSTHROUGH", "send_hops_seed": "PASSTHROUGH", "send_is_seed": "PASSTHROUGH",
}


def _ok(x):
    return x is not None and not (isinstance(x, float) and math.isnan(x))


# A reason is only shown if the feature VALUES also make it true (guards against odd contributions)
CONDITIONS = {
    "AMOUNT": lambda f: f["amt_over_median"] > 2,
    "NEW_PAYEE": lambda f: f["is_new_payee"] == 1,
    "NEW_DEVICE": lambda f: f["is_new_device"] == 1,
    "LOCATION": lambda f: f["dist_km"] > 50,
    "TIME": lambda f: f["hour_dev"] > 3,
    "BURST": lambda f: f["new_payees_1h"] >= 2 or f["vel_1h"] >= 3,
    "COLLECT": lambda f: f["is_collect"] == 1,
    "CALL": lambda f: f["call_active"] == 1,
    "SCREEN": lambda f: f["screen_share"] == 1,
    "QR": lambda f: f["qr_mismatch"] == 1,
    "NEW_ACCOUNT": lambda f: f["recv_age_days"] < 14,
    "MULE_PATTERN": lambda f: f["recv_uniq_senders_24h"] >= 3 or (_ok(f["recv_pt_min_24h"]) and f["recv_pt_min_24h"] < 1800),
    "KNOWN_MULE": lambda f: f["recv_hops_seed"] <= 2,
    "PASSTHROUGH": lambda f: _ok(f["send_since_last_in"]) and f["send_since_last_in"] < 3600,
}


def explain(contribs, feat_values, feature_names, k=3, min_weight=0.05, user_facing=False):
    """contribs: SHAP values (log-odds) aligned with feature_names. Returns top-k reasons.
    user_facing=True drops analyst-only reasons (PASSTHROUGH)."""
    f = dict(zip(feature_names, feat_values))
    groups = {}
    for name, c in zip(feature_names, contribs):
        rid = FEATURE_TO_REASON.get(name)
        if rid and c > 0:
            groups[rid] = groups.get(rid, 0.0) + float(c)
    out = []
    for rid, w in sorted(groups.items(), key=lambda kv: -kv[1]):
        if w < min_weight or (user_facing and rid == "PASSTHROUGH"):
            continue
        try:
            if not CONDITIONS[rid](f):
                continue
        except (KeyError, TypeError):
            continue
        out.append({"id": rid, "weight": round(w, 3), "text": REASONS[rid]})
        if len(out) == k:
            break
    return out