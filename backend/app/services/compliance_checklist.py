# app/services/compliance_checklist.py
"""
Compliance checklist requirements for marketing materials.
Exact checklist as provided by compliance department.
"""
from typing import List, Tuple

GENERAL_REQUIREMENTS = """
GENERAL REQUIREMENTS FOR ALL MARKETING MATERIALS - (APPLICABLE TO ALL COUNTRIES)
There are several general requirements for the contents of all Marketing Materials (whether general in nature or relating to a specific Product) as follows:
- All Marketing Materials must be clear, fair and not misleading. This means that the purpose of the promotion, the nature or type of the FMPs business and the type of the Products being promoted (if applicable) must be clear.
- Any statement, promise or forecast must be fair and not misleading in the form and context in which it appears. If any promise or forecast is based on assumptions, the assumptions must be stated. Marketing Materials must not forecast the possible future price of a particular security.
- Marketing Materials must not include false or misleading statements.
- All Marketing Materials that contain any reference to past performance must include a statement that past performance is not necessarily an indicator of future results.*
- A statement regarding investment risks associated with the Products to be promoted must be included within the Marketing Materials.*
- The intended investor type needs to be specified. For example, all Marketing Materials for Products that are intended only for Professional Investors in the UAE will have this clearly stated. (Note: This is covered via an automated email disclaimer).
- Where inclusion of a specific statement is required (illustrated by an * above and throughout this document), such statement is included within Greenstone's standard disclaimers. Where alternative disclaimers are utilised or amendments are made to Greenstone's standard disclaimers at the request of the Fund Manager Partner, care must be taken to ensure that such disclaimer language fulfils the above listed requirements. Disclaimers for inclusion in Marketing Materials must be approved by Compliance and the FMP.
- Marketing Materials should not include language that presents as an opinion, representation or recommendation of Greenstone or any of its employees.
"""

PRE_MARKETING_REQUIREMENTS = """
REQUIREMENTS FOR PRE-MARKETING MATERIALS - (APPLICABLE TO ALL COUNTRIES)
- Pre-marketing materials should be restricted to factual information about the Fund Manager Partner, including its strategies, track record and team bios, information on previous funds (vintages, strategies, fund sizes, returns, etc.). Pre-marketing materials must not contain reference to any particular future investment opportunities or contain any terms of investment for an investment opportunity.
- Unless otherwise agreed with the FMP, Greenstone's standard pre-marketing disclaimers should be utilised and any country specific disclaimers should be removed unless such language is in line with the pre-marketing disclaimer as approved by Compliance.
"""

UAE_SCA_REQUIREMENTS = """
ADDITIONAL REQUIREMENTS FOR MARKETING SCA REGISTERED PRODUCTS IN THE UAE (DISCLAIMER-SPECIFIC)
Once Compliance confirm that a Product is registered with the SCA (an "SCA-Registered Product"), Greenstone will be required to comply with the financial promotions procedures set out in the Chairman of the Authority's Board of Directors' Decision No. (13/Chairman) of 2021 on the Regulations Manual of the Financial Activities and Status regularization Mechanisms, specifically Section 3, Chapter 5, Article 6 - "The Promoter's Obligations Relating to the Information provided to its client upon Promotion".

When marketing an SCA-Registered Product in the UAE, the following specific details (in addition to the general requirements above) need to be included within the Marketing Materials for that Product:
- The type and place of issue of the Products to be promoted, as well as the number of issued Products, currency of the issue and all related information.*
- A statement whether the promoted Products are listed, and if yes, include a list of the markets where they are listed.*
- A minimum limit for subscription or purchase, and any ban or restriction on the investor, its trading or subscription.*
- Mechanisms of dividends distribution and redemption as well as maturity dates, if applicable.
- The type of investor appropriate to the Products (ordinary, professional or counterparty)*
- The investment risks associated with the Products to be promoted*
- A statement identifying the major shareholders of the issuer and/or the foreign issuer who own 10% or more of the shares.
- The method of communication used by the entities concerned with the subscription, selling and purchasing of the promoted Products.*
- Details of the Shariah Supervisory Board that will ensure that the promoted Products are Shariah-compliant (if applicable).
- The mechanisms and means for disclosing data and information.
- The name, address, contact details and regulatory status of the appointed SCA licensed promoter.*
"""

DIFC_REQUIREMENTS = """
ADDITIONAL GENERAL REQUIREMENTS FOR MARKETING MATERIALS IN THE DIFC (DISCLAIMER-SPECIFIC)
Marketing Materials must for distribution to investors within the DIFC must include:
- include the name, legal and licensing status of Greenstone (DIFC) Limited*;
- a clear statement of the financial services Greenstone (DIFC) Limited is licensed or approved to provide*;
- a clear statement that the marketing material is intended only for Professional Clients or Market Counterparties and that no other Person should act upon it*;
- Marketing Materials must not publish or disclose any future expectations based on past performance or any other assumptions without appropriate disclosures;
- Marketing Materials must be distributed appropriately to the target recipients, and therefore marketing materials intended only for Professional Clients must not be distributed to Retail Clients.
"""

KSA_REQUIREMENTS = """
ADDITIONAL GENERAL REQUIREMENTS FOR MARKETING MATERIALS IN THE KSA
- Marketing Materials must not include false or misleading statements relating to the securities business, size or resources of Greenstone Saudi Arabia;
- Marketing Materials need to include the name, address, and regulatory status of Greenstone Saudi Arabia. (Note: This is covered via an automated email disclaimer).

In accordance with Annex 5.1 of the Capital Market Institution Regulations issued by the Capital Market Authority ("KSA CMA") Board Resolution No.1-83-2005 as amended, when promoting Products in the KSA the contents of the Marketing Materials need to comply with the following product-specific requirements (in addition to the general requirements above, DISCLAIMER-SPECIFIC):
- The Marketing Materials must not describe a Product as guaranteed unless there is a legally enforceable arrangement with a third party who undertakes to meet in full an investor's claim under the guarantee.
- Comparisons between different Products must:
  - be based on facts verified by Greenstone Saudi Arabia or on assumptions stated within the Marketing Materials;
  - be presented in a fair and balanced way; and
  - not omit anything material to the comparison.
- The Marketing Materials need to include a statement acknowledging material interests of Greenstone Saudi Arabia or its affiliates if they:
  - have or may have a position or holding in the Product concerned or in related Products; or
  - are providing or have provided within the previous 12 months significant advice or securities business services to the issuer of the Product concerned or of a related Product.
- Any information about the past performance of Products or of Greenstone Saudi Arabia or its affiliates that is included in the Marketing Materials must:
  - be a fair representation of the past performance;
  - not be selected so as to exaggerate performance;
  - state the source of the information;
  - be based on verifiable information; and
  - warn that past performance is not necessarily a guide to future performance.
- If the Marketing Materials contain any reference to the impact of zakat or taxation, they must state the assumed rate of zakat or taxation and any relief; and state that such rates and relief may change over time.
- If cancellation rights apply to the Products to be promoted, the Marketing Materials must contain details of such rights, including the period within which they may be exercised.
- In addition to the above specific requirements, the Capital Market Institution Regulations prescribe risk warnings that need to be included when marketing specific Products. Such warnings include, inter alia, statements in relation to fluctuations in price or value, risks in relation to illiquid securities, adverse effects of changes in currency rates on the value, price or income of the Product. The required warning statements shall be included within Greenstone's standard disclaimers. Where alternative disclaimers are utilised or amendments are made to Greenstone's standard disclaimers at the request of the Fund Manager Partner, care must be taken to ensure that such disclaimer language fulfils the above listed requirements.
- For all Products registered with the KSA CMA, the Compliance team will also check that names of the Product and the FMP in the Marketing Materials match those in the registration application and the mandate. The Compliance team will also review if any information provided within the Marketing Materials (including the minimum commitment and fund size) differs to the information submitted to the KSA CMA within the fund registration documents.
"""

KUWAIT_REQUIREMENTS = """
ADDITIONAL REQUIREMENTS FOR MARKETING IN KUWAIT
Until a Product has been registered with the Kuwait CMA, materials issued to Kuwait based investors must only present factual, non-promotional information. Such communications may include generic information about a FMP, including its strategies, track record and team bios, information on previous funds (vintages, strategies, fund sizes, returns, etc.), and limited factual (non-promotional) information about any Product which may be marketed outside Kuwait at the time and may be marketed in Kuwait in the future.

In accordance with Chapter Seven of Module Eight (Conduct of Business) of the Executive Bylaws issued by the Kuwait CMA, the contents of the Marketing Materials need to comply with the following requirements (in addition to the general requirements above):
- Marketing Materials must include a phrase, in clearly identifiable font, referencing that the document was prepared for promotional purposes.
- Marketing Materials must include the name of the CMA Licensed entity who has approved the content of the Marketing Materials
- Marketing Materials must include an undertaking that the content does not disguise, diminish or obscure important items in relation to the Product.
- For all Products registered with the Kuwait CMA, the Compliance team will also check that names of the Product and the FMP in the Marketing Materials match those in the registration application and the mandate. The Compliance team will also review if any information provided within the Marketing Materials (including the minimum commitment and fund size) differs to the information submitted to the Kuwait CMA within the fund registration documents.
"""


def get_checklist_for_jurisdiction(jurisdiction: str = None) -> str:
    """
    Get relevant compliance checklist based on jurisdiction.
    
    Args:
        jurisdiction: Jurisdiction name (Oman, Qatar, DIFC, KSA, UAE, Kuwait) or None for general only
        
    Returns:
        Formatted checklist string
    """
    checklist = GENERAL_REQUIREMENTS + "\n\n"
    
    # Only add region-specific requirements if jurisdiction is specified (not None/General)
    if jurisdiction and jurisdiction.upper() != "GENERAL":
        jurisdiction_upper = jurisdiction.upper()
        if "UAE" in jurisdiction_upper or jurisdiction_upper == "UAE":
            checklist += UAE_SCA_REQUIREMENTS + "\n\n"
        if "DIFC" in jurisdiction_upper:
            checklist += DIFC_REQUIREMENTS + "\n\n"
        if "KSA" in jurisdiction_upper or "SAUDI" in jurisdiction_upper:
            checklist += KSA_REQUIREMENTS + "\n\n"
        if "KUWAIT" in jurisdiction_upper:
            checklist += KUWAIT_REQUIREMENTS + "\n\n"
    
    return checklist


def parse_checklist_items(checklist_text: str) -> List[tuple[str, str, bool]]:
    """
    Parse checklist text into individual items.
    
    Args:
        checklist_text: Full checklist text
        
    Returns:
        List of (item_text, section_name, is_required) tuples
    """
    items = []
    current_section = "GENERAL REQUIREMENTS"
    
    lines = checklist_text.split('\n')
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Detect section headers
        if "GENERAL REQUIREMENTS" in line.upper():
            current_section = "GENERAL REQUIREMENTS"
            continue
        elif "PRE-MARKETING" in line.upper():
            current_section = "PRE-MARKETING REQUIREMENTS"
            continue
        elif "UAE" in line.upper() and "REQUIREMENTS" in line.upper():
            current_section = "UAE SCA REQUIREMENTS"
            continue
        elif "DIFC" in line.upper() and "REQUIREMENTS" in line.upper():
            current_section = "DIFC REQUIREMENTS"
            continue
        elif "KSA" in line.upper() and "REQUIREMENTS" in line.upper():
            current_section = "KSA REQUIREMENTS"
            continue
        elif "KUWAIT" in line.upper() and "REQUIREMENTS" in line.upper():
            current_section = "KUWAIT REQUIREMENTS"
            continue
        
        # Parse checklist items (lines starting with -)
        if line.startswith('-'):
            item_text = line[1:].strip()
            # Check if required (marked with *)
            is_required = '*' in item_text
            # Remove * from text
            item_text = item_text.replace('*', '').strip()
            if item_text:
                items.append((item_text, current_section, is_required))
    
    return items
