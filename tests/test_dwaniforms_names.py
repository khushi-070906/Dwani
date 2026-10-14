"""Indian name matcher: "Mohd Aslam", "Mohammad Aslam" and "मोहम्मद अस्लम" are one person, and two different people are
NOT said to be one."""
import pytest

from dwaniforms import names


# ---- names ---------------------------------------------------------------------------------------------------------------
SAME = [("Mohd Aslam", "Mohammad Aslam"), ("Mohd Aslam", "मोहम्मद अस्लम"), ("Mohammad Aslam", "मोहम्मद असलम"),
        ("Sharma Anil", "Anil Sharma"), ("Anil Kumar Sharma", "Anil Sharma"), ("Shri Rajesh Verma S/O Mohan Lal", "Rajesh Varma"),
        ("Priya Devi", "प्रिया देवी"), ("Savita Srivastava", "सविता श्रीवास्तव"), ("Gurpreet Singh", "Gurpreet Sinh"),
        ("Mohd. Ashraf Khan", "Mohammed Ashraf Khan"), ("Ram Prasad Yadav", "राम प्रसाद यादव"), ("Fatima Khatoon", "Fatma Khatun"),
        ("Ravi Shankar", "Ravishankar"), ("Ashok Kumar", "Asok Kumar"), ("Laxmi Devi", "Lakshmi Devi"), ("सुरेश कुमार", "Suresh Kumar"),
        ("Sandeep Kaur", "Sandip Kour"), ("Pooja Rani", "Puja Rani"), ("Deepak Sharma", "Dipak Sharma"),
        ("ਗੁਰਪ੍ਰੀਤ ਸਿੰਘ", "Gurpreet Singh")]
ASK = [("Anil Kumar Sharma", "A. K. Sharma"), ("Aslam", "Mohd Aslam"), ("Sunil Kumar", "Sushil Kumar"),
       ("Rahul Singh", "Rahil Singh"), ("Ramesh Kumar", "Rakesh Kumar"), ("முருகன் குமார்", "Murugan Kumar"), ("Manoj Kumar", "Manoj Kumari")]
DIFFERENT = [("Suresh Gupta", "Mahesh Gupta"), ("Mohd Aslam", "Mohd Salim"), ("Anil Sharma", "Rajesh Verma"),
             ("A K", "Anil Kumar"), ("Sita Devi", "Gita Devi"), ("", "Anil")]


@pytest.mark.parametrize("a,b", SAME)
def test_same_person(a, b):
    r = names.match(a, b)
    assert r["verdict"] == "same", r
    assert names.match(b, a)["verdict"] == "same", "order of the two names must not matter"


@pytest.mark.parametrize("a,b", ASK)
def test_close_but_not_sure_means_ask(a, b):
    assert names.match(a, b)["verdict"] == "probably", names.match(a, b)


@pytest.mark.parametrize("a,b", DIFFERENT)
def test_different_people(a, b):
    assert names.match(a, b)["verdict"] == "different", names.match(a, b)


def test_name_answer_explains_itself_and_shows_one_spelling():
    r = names.match("Mohd Aslam", "मोहम्मद अस्लम")
    assert r["a"] == r["b"] == "Muhammad Aslam" and r["score"] == 1.0 and r["reasons"]
    assert names.canonical("Shri Ram Prasad S/O Lal Chand") == "Ram Prasad"
    assert names.same_person("Mohd Aslam", "Mohammad Aslam") and not names.same_person("Aslam", "Salim")


def test_other_indian_scripts_are_read_by_sound():
    assert names.sound("குமார்") == names.sound("कुमार") == names.sound("Kumar")
