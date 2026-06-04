#!/usr/bin/env python3
"""
Cucumber Recruitment — Batch Graphic Generator
5 copy variations per event. Subtext kept short (5–8 words) to match reference style.

Run: python generate_batch.py
"""

from pathlib import Path
from generate_graphic import generate

OUT_DIR = Path(__file__).parent / "graphics"

EVENTS = {

    "Volunteers_Week_Kickoff": {
        "label": "Volunteers' Week Kick-Off (1 June)",
        "variants": [
            ("VOLUNTEERS' WEEK STARTS TODAY.",   "THANK YOU TO EVERY VOLUNTEER IN HEALTHCARE."),
            ("THANK YOU, VOLUNTEERS.",           "YOU DON'T WEAR A UNIFORM. YOU STILL SHOW UP."),
            ("IT STARTS WITH YOU.",              "HAPPY VOLUNTEERS' WEEK FROM CUCUMBER RECRUITMENT."),
            ("UNPAID. UNSUNG. UNMISSABLE.",      "THANK YOU FOR EVERYTHING YOU DO."),
            ("THE HEART OF HEALTHCARE.",         "CELEBRATING EVERY VOLUNTEER THIS VOLUNTEERS' WEEK."),
        ],
    },

    "Volunteers_Week": {
        "label": "Volunteers' Week — Above & Beyond (3 June)",
        "variants": [
            ("ABOVE AND BEYOND.",                "WE SEE EVERY NURSE AND CARER WHO GIVES MORE."),
            ("IT'S VOLUNTEERS' WEEK.",           "CELEBRATING THE PEOPLE WHO SHOW UP FOR EVERYTHING."),
            ("YOU DO MORE THAN MOST.",           "HAPPY VOLUNTEERS' WEEK FROM CUCUMBER RECRUITMENT."),
            ("SOME PEOPLE JUST GIVE MORE.",      "THIS WEEK WE CELEBRATE YOU."),
            ("HEALTHCARE RUNS ON PEOPLE LIKE YOU.", "THANK YOU. FROM ALL OF US AT CUCUMBER."),
        ],
    },

    "Why_Cucumber": {
        "label": "Why Cucumber — Brand Post (4 June)",
        "variants": [
            ("WE TREAT YOU LIKE A PERSON.",      "NOT A PLACEMENT. A REAL PARTNERSHIP."),
            ("THIS IS WHY NURSES CHOOSE CUCUMBER.", "WEEKLY PAY. FLEXIBLE SHIFTS. REAL SUPPORT."),
            ("MORE THAN AN AGENCY.",             "WE'VE GOT YOUR BACK FROM DAY ONE."),
            ("YOUR WORK. YOUR TERMS.",           "BUILT AROUND YOUR LIFE, NOT OURS."),
            ("NURSES STAY WITH CUCUMBER.",       "BECAUSE WE SHOW UP FOR YOU. EVERY TIME."),
        ],
    },

    "Weekend_Recruitment_Push": {
        "label": "Weekend Recruitment Push (5 June)",
        "variants": [
            ("THINKING ABOUT YOUR NEXT ROLE?",   "DROP US A MESSAGE. WE'D LOVE TO CHAT."),
            ("GREAT SHIFTS START HERE.",         "WEEKLY PAY. FLEXIBLE HOURS. REAL SUPPORT."),
            ("YOUR NEXT OPPORTUNITY IS WAITING.", "NURSES AND HCAS — APPLY TODAY."),
            ("WORK WITH AN AGENCY THAT GETS IT.", "WEEKLY PAY. FLEXIBLE SHIFTS. CUCUMBER RECRUITMENT."),
            ("HEALTHCARE ROLES ACROSS THE UK.",  "REGISTER WITH CUCUMBER RECRUITMENT TODAY."),
        ],
    },

    "Carers_Week": {
        "label": "Carers' Week (8–14 June)",
        "variants": [
            ("CARING IS EVERYTHING.",            "HAPPY CARERS' WEEK FROM CUCUMBER RECRUITMENT."),
            ("CARERS MAKE THE WORLD GO ROUND.",  "WE CELEBRATE EVERY PERSON WHO SHOWS UP TO CARE."),
            ("IT TAKES HEART TO CARE.",          "PROUD TO SUPPORT CARERS ACROSS THE UK."),
            ("THANK YOU, CARERS.",               "THE WORK YOU DO IS EVERYTHING. WE SEE YOU."),
            ("BEHIND EVERY PATIENT IS A CARER.", "HAPPY CARERS' WEEK — CELEBRATING THE PEOPLE WHO GIVE EVERYTHING."),
        ],
    },

    "Mens_Health_Week": {
        "label": "Men's Health Week (14–21 June)",
        "variants": [
            ("YOUR HEALTH MATTERS TOO.",         "LOOK AFTER YOURSELF, NOT JUST YOUR PATIENTS."),
            ("CHECK IN WITH YOURSELF.",          "MEN'S HEALTH WEEK — LET'S TALK."),
            ("STRONG ENOUGH TO ASK FOR HELP.",   "CUCUMBER RECRUITMENT STANDS WITH YOU."),
            ("MEN'S HEALTH WEEK 2026.",          "YOUR WELLBEING MATTERS TO US."),
            ("TAKE CARE OF YOURSELF.",           "THIS WEEK IS FOR YOU."),
        ],
    },

    "Learning_Disability_Week": {
        "label": "Learning Disability Week (15–22 June)",
        "variants": [
            ("EVERY PERSON COUNTS.",             "CELEBRATING EVERY INDIVIDUAL WE SUPPORT."),
            ("INCLUSION IS OUR STANDARD.",       "LEARNING DISABILITY WEEK WITH CUCUMBER RECRUITMENT."),
            ("CARE WITHOUT LIMITS.",             "PROUD OF EVERY TEAM MEMBER WHO SHOWS UP."),
            ("SEE THE PERSON. NOT THE LABEL.",   "LEARNING DISABILITY WEEK 2026 — FROM CUCUMBER."),
            ("PROUD TO SUPPORT SPECIALIST CARE.", "CELEBRATING OUR LEARNING DISABILITY TEAMS."),
        ],
    },

    "Pride_Month": {
        "label": "Pride Month (July)",
        "variants": [
            ("INCLUSION IS EVERYTHING.",         "HAPPY PRIDE MONTH FROM CUCUMBER RECRUITMENT."),
            ("PRIDE IN HEALTHCARE.",             "WE CELEBRATE EVERY PERSON WHO CARES."),
            ("BE YOURSELF AT WORK.",             "CUCUMBER RECRUITMENT IS AN INCLUSIVE EMPLOYER."),
            ("WE HIRE EVERYONE.",                "DIVERSITY MAKES US STRONGER IN HEALTHCARE."),
            ("LOVE WHAT YOU DO.",                "FIND YOUR PERFECT HEALTHCARE ROLE THIS PRIDE MONTH."),
        ],
    },

    "National_Inclusion_Week": {
        "label": "National Inclusion Week (14–22 Sept)",
        "variants": [
            ("INCLUSION STARTS HERE.",           "CUCUMBER CELEBRATES EVERY VOICE."),
            ("DIFFERENT BACKGROUNDS. ONE TEAM.", "PROUD TO BE AN INCLUSIVE EMPLOYER."),
            ("EVERYONE BELONGS HERE.",           "BUILT ON RESPECT, DIVERSITY AND CARE."),
            ("INCLUSIVE HEALTHCARE STARTS WITH US.", "NATIONAL INCLUSION WEEK 2026."),
            ("DIVERSITY IS OUR STRENGTH.",       "HAPPY NATIONAL INCLUSION WEEK FROM CUCUMBER."),
        ],
    },

    "World_Heart_Day": {
        "label": "World Heart Day (29 Sept)",
        "variants": [
            ("LOOK AFTER YOUR HEART.",           "CARE FOR YOURSELF, NOT JUST YOUR PATIENTS."),
            ("YOUR HEART MATTERS.",              "TAKE A MOMENT FOR YOUR OWN WELLBEING TODAY."),
            ("HEALTHCARE STARTS WITH YOU.",      "WORLD HEART DAY 2026 — FROM CUCUMBER RECRUITMENT."),
            ("YOU LOOK AFTER EVERYONE. NOW LOOK AFTER YOURSELF.", "YOUR HEART HEALTH MATTERS TO US."),
            ("STRONG HEARTS SAVE LIVES.",        "LOOK AFTER YOURS AS WELL AS YOUR PATIENTS."),
        ],
    },

    "Emergency_Nurses_Day": {
        "label": "Emergency Nurses Day (8 Oct)",
        "variants": [
            ("EMERGENCY NURSES DAY.",            "THE TOUGHEST, MOST ESSENTIAL ROLE IN HEALTHCARE."),
            ("NO TWO SHIFTS ARE THE SAME.",      "YOUR COURAGE SAVES LIVES EVERY DAY."),
            ("WHEN IT MATTERS MOST, THEY'RE THERE.", "HAPPY EMERGENCY NURSES DAY FROM CUCUMBER."),
            ("CALM UNDER PRESSURE.",             "CELEBRATING EMERGENCY NURSES EVERYWHERE TODAY."),
            ("EVERY SECOND COUNTS.",             "THANK YOU FOR SHOWING UP EVERY TIME."),
        ],
    },

    "World_Mental_Health_Day": {
        "label": "World Mental Health Day (10 Oct)",
        "variants": [
            ("MENTAL HEALTH MATTERS.",           "CUCUMBER OFFERS 24/7 SUPPORT FOR ALL OUR STAFF."),
            ("HOW ARE YOU, REALLY?",             "IT'S OK TO NOT BE OK."),
            ("YOU CARE FOR EVERYONE. WE CARE FOR YOU.", "24/7 SUPPORT. QUARTERLY SUPERVISIONS."),
            ("IT'S OK TO ASK FOR HELP.",         "CUCUMBER IS HERE 24 HOURS A DAY."),
            ("NURSING DOESN'T STOP AT 5PM.",     "AND NEITHER DO WE."),
        ],
    },

    "National_Stress_Awareness_Day": {
        "label": "National Stress Awareness Day (4 Nov)",
        "variants": [
            ("STRESS IS REAL. SO IS SUPPORT.",   "WE ARE HERE FOR YOU, DAY AND NIGHT."),
            ("YOU DON'T HAVE TO DO IT ALONE.",   "24/7 SUPPORT AND QUARTERLY SUPERVISIONS."),
            ("DIFFICULT SHIFT? WE'VE GOT YOU.",  "REAL SUPPORT FOR REAL NURSES."),
            ("WE CHECK IN PROPERLY.",            "EVERY 3 MONTHS. A REAL CONVERSATION."),
            ("TAKE A BREATH.",                   "CUCUMBER RECRUITMENT SUPPORTS YOUR WELLBEING."),
        ],
    },

    "World_Diabetes_Day": {
        "label": "World Diabetes Day (14 Nov)",
        "variants": [
            ("DIABETES CARE DONE RIGHT.",        "CELEBRATING OUR SPECIALIST NURSING TEAMS."),
            ("EXPERT CARE. EVERY DAY.",          "PROUD OF OUR DIABETES-SPECIALIST NURSES."),
            ("KNOWLEDGE SAVES LIVES.",           "OUR NURSES BRING EXPERTISE TO EVERY SHIFT."),
            ("SPECIALIST NURSES. EXCEPTIONAL CARE.", "HAPPY WORLD DIABETES DAY FROM CUCUMBER."),
            ("BEYOND THE BASICS.",               "CELEBRATING NURSES WITH SPECIALIST DIABETES SKILLS."),
        ],
    },

    "Anti_Bullying_Week": {
        "label": "Anti-Bullying Week (16–21 Nov)",
        "variants": [
            ("RESPECT IS NON-NEGOTIABLE.",       "CUCUMBER STANDS FOR A SAFE WORKPLACE."),
            ("SPEAK UP. WE'RE LISTENING.",       "EVERY VOICE DESERVES TO BE HEARD."),
            ("SAFE TO SPEAK. SAFE TO WORK.",     "OUR COMMITMENT TO EVERY TEAM MEMBER."),
            ("KINDNESS IS CONTAGIOUS.",          "CUCUMBER CHAMPIONS A RESPECTFUL WORKPLACE."),
            ("BULLYING HAS NO PLACE HERE.",      "STANDING TOGETHER FOR A KINDER HEALTHCARE SECTOR."),
        ],
    },

    "World_AIDS_Day": {
        "label": "World AIDS Day (1 Dec)",
        "variants": [
            ("STANDING TOGETHER.",               "CUCUMBER SHOWS SOLIDARITY WITH ALL THOSE AFFECTED."),
            ("AWARENESS SAVES LIVES.",           "COMPASSIONATE CARE FOR EVERY PATIENT."),
            ("COMPASSION WITHOUT EXCEPTION.",    "OUR NURSES DELIVER CARE WITH DIGNITY."),
            ("NO STIGMA. JUST CARE.",            "WORLD AIDS DAY 2026 — FROM CUCUMBER RECRUITMENT."),
            ("HEALTHCARE IS FOR EVERYONE.",      "CELEBRATING NURSES WHO CARE WITHOUT JUDGMENT."),
        ],
    },
}


def run():
    total = sum(len(v["variants"]) for v in EVENTS.values())
    print(f"Generating {total} graphics across {len(EVENTS)} events...\n")
    done = 0
    for folder_name, event in EVENTS.items():
        event_dir = OUT_DIR / folder_name
        event_dir.mkdir(parents=True, exist_ok=True)
        print(f"  {event['label']}")
        for i, (headline, subtext) in enumerate(event["variants"], start=1):
            out_path = event_dir / f"v{i}.png"
            generate(headline, subtext, "cucumber-recruitment.co.uk", str(out_path))
            done += 1
        print()
    print(f"Done. {done} graphics saved to: {OUT_DIR}")


if __name__ == "__main__":
    run()
