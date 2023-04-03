# imports
from mockfirestore import MockFirestore
import pytest
import os
import sys
sys.path.append("../../../")
from dotenv import load_dotenv
load_dotenv()

# Test firebase.py
from common.utils.firebase import *

def test_add_hearts_code_reliability_for_user():
    db = get_db()    
    db.collection("users").document("test").set(
        {"history": {"how": {"code_reliability": 0}, "what": {"code_quality": 0}}})
    
    assert db.collection("users").document("test").get().to_dict()[
        "history"]["how"]["code_reliability"] == 0
    
    add_hearts_for_user("test", 1, "code_reliability")    

    assert db.collection("users").document("test").get().to_dict()[
        "history"]["how"]["code_reliability"] == 1
    
    add_hearts_for_user("test", 1, "code_reliability")
    
    assert db.collection("users").document("test").get().to_dict()[
        "history"]["how"]["code_reliability"] == 2

def test_add_hearts_customer_driven_innovation_and_design_thinking_for_user():
    db = get_db()    
    db.collection("users").document("test").set(
        {"history": {"how": {"customer_driven_innovation_and_design_thinking": 0}, "what": {"code_quality": 0}}})
    
    assert db.collection("users").document("test").get().to_dict()[
        "history"]["how"]["customer_driven_innovation_and_design_thinking"] == 0
    
    add_hearts_for_user("test", 1, "customer_driven_innovation_and_design_thinking")    

    assert db.collection("users").document("test").get().to_dict()[
        "history"]["how"]["customer_driven_innovation_and_design_thinking"] == 1
    
    add_hearts_for_user("test", 1, "customer_driven_innovation_and_design_thinking")
    
    assert db.collection("users").document("test").get().to_dict()[
        "history"]["how"]["customer_driven_innovation_and_design_thinking"] == 2