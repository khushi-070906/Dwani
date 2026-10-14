"""Indian address parser: "H-24, gali no. 3, near Shiv mandir, Uttam Nagar" is broken into house, street, landmark and
locality, and nothing is invented."""
from dwaniforms import address


# ---- addresses -----------------------------------------------------------------------------------------------------------
def test_the_example_from_the_brief():
    p = address.parse("H-24, gali no. 3, near Shiv mandir, Uttam Nagar")
    assert (p["house"], p["street"], p["landmark"], p["locality"]) == ("H-24", "Gali No. 3", "Shiv Mandir", "Uttam Nagar")
    assert p["landmark_relation"] == "near" and p["unparsed"] == []


def test_the_same_address_without_any_commas():
    p = address.parse("H-24 gali no 3 near shiv mandir uttam nagar")
    assert (p["house"], p["street"], p["landmark"], p["locality"]) == ("H-24", "Gali No. 3", "Shiv Mandir", "Uttam Nagar")


def test_city_state_and_pincode():
    p = address.parse("H-24, gali no. 3, near Shiv mandir, Uttam Nagar, New Delhi 110059")
    assert (p["city"], p["state"], p["pincode"]) == ("New Delhi", "Delhi", "110059")
    assert address.missing(p) == []


def test_hindi_address():
    p = address.parse("एच-24, गली नंबर 3, शिव मंदिर के पास, उत्तम नगर, नई दिल्ली 110059")
    assert p["street"] == "गली नंबर 3" and p["landmark"] == "शिव मंदिर" and p["locality"] == "उत्तम नगर"
    assert p["pincode"] == "110059" and p["state"] == "Delhi" and p["landmark_relation"] == "near"


def test_hindi_digits_in_pincode_and_house():
    p = address.parse("मकान नंबर १२, उत्तम नगर, नई दिल्ली ११००५९")
    assert p["house"] == "12" and p["pincode"] == "110059"


def test_village_address():
    p = address.parse("Vill Rampur, PO Sikandrabad, Teh. Sadar, Dist Bulandshahr, UP 203205")
    assert (p["village"], p["post_office"], p["tehsil"], p["district"], p["state"], p["pincode"]) == (
        "Rampur", "Sikandrabad", "Sadar", "Bulandshahr", "Uttar Pradesh", "203205")
    assert "house" not in p


def test_village_address_in_hindi():
    p = address.parse("मकान नंबर 12, ग्राम सिकंदरपुर, पोस्ट खुर्जा, जिला बुलंदशहर, उत्तर प्रदेश")
    assert (p["house"], p["village"], p["post_office"], p["district"], p["state"]) == (
        "12", "सिकंदरपुर", "खुर्जा", "बुलंदशहर", "Uttar Pradesh")


def test_block_sector_floor_and_care_of():
    p = address.parse("C/o Ramesh Kumar, Flat 302, 2nd floor, Green Park Apartments, MG Road, Pune, Maharashtra 411001")
    assert p["care_of"] == "Ramesh Kumar" and p["house"] == "302" and p["floor"] == "2nd Floor"
    assert p["city"] == "Pune" and p["state"] == "Maharashtra" and p["pincode"] == "411001"
    q = address.parse("H.No. 45/2, Block C, Sector 12, Rohini, Delhi - 110085")
    assert q["house"] == "45/2" and q["block"] == "Block C, Sector 12" and q["locality"] == "Rohini"


def test_landmark_words_in_other_positions():
    assert address.parse("123 Ashok Vihar Phase 2 opposite petrol pump Delhi")["landmark_relation"] == "opposite"
    p = address.parse("plot no 7, shiv colony, behind sbi bank, jaipur rajasthan 302001")
    assert p["landmark_relation"] == "behind" and p["locality"] == "Shiv Colony" and p["city"] == "Jaipur"


def test_a_pincode_is_six_digits_not_any_number():
    assert "pincode" not in address.parse("H-24, Uttam Nagar, 12345")
    assert address.parse("Hisar, Haryana 125001")["pincode"] == "125001"


def test_nothing_is_invented_for_an_empty_or_odd_text():
    assert address.parse("") == {"unparsed": []}
    assert address.parse("   ,, ") == {"unparsed": []}
    assert address.missing(address.parse("")) == ["house_or_village", "city_or_district", "state", "pincode"]


def test_format_is_one_clean_line_without_repeats():
    line = address.format_address(address.parse("H.No. 45/2, Block C, Sector 12, Rohini, Delhi - 110085"))
    assert line == "45/2, Block C, Sector 12, Rohini, Delhi, 110085"
    assert address.format_address(address.parse("H-24, gali no. 3, near Shiv mandir, Uttam Nagar")) == \
        "H-24, Gali No. 3, Near Shiv Mandir, Uttam Nagar"
