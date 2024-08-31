import requests
import json
from datetime import datetime

def search_nonprofits(query, state, c_code, min_revenue=None, max_revenue=None, ntee=None):
    base_url = "https://projects.propublica.org/nonprofits/api/v2/search.json"
    params = {
        "q": query,
        "state[id]": state,
        "c_code[id]": c_code
    }
    
    if ntee:
        params["ntee[id]"] = ntee
    
    response = requests.get(base_url, params=params)
    if response.status_code == 200:
        results = response.json()
        filtered_results = []
        for org in results.get('organizations', []):
            if min_revenue is not None and org.get('income_amount', 0) < min_revenue:
                continue
            if max_revenue is not None and org.get('income_amount', 0) > max_revenue:
                continue
            filtered_results.append(org)
        results['organizations'] = filtered_results
        return results
    else:
        print(f"Error in search request: {response.status_code}")
        return None

def get_organization_details(ein):
    base_url = f"https://projects.propublica.org/nonprofits/api/v2/organizations/{ein}.json"
    
    response = requests.get(base_url)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error in organization details request: {response.status_code}")
        return None

def print_organization_info(org):
    print(f"Name: {org.get('name', 'N/A')}")
    print(f"EIN: {org.get('ein', 'N/A')}")
    print(f"STREIN: {org.get('strein', 'N/A')}")
    print(f"City: {org.get('city', 'N/A')}")
    print(f"State: {org.get('state', 'N/A')}")
    print(f"NTEE Code: {org.get('ntee_code', 'N/A')}")
    print(f"Subsection Code: {org.get('subseccd', 'N/A')}")
    print(f"Classification Codes: {org.get('classification_codes', 'N/A')}")
    print(f"Ruling Date: {org.get('ruling_date', 'N/A')}")
    print(f"Income Amount: ${org.get('income_amount', 'N/A')}")
    print("---")

def print_filing_info(filing):
    print(f"Tax Period: {filing.get('tax_prd', 'N/A')}")
    print(f"Total Revenue: ${filing.get('totrevenue', 'N/A')}")
    print(f"Total Expenses: ${filing.get('totfuncexpns', 'N/A')}")
    print(f"Total Assets: ${filing.get('totassetsend', 'N/A')}")
    print(f"Total Liabilities: ${filing.get('totliabend', 'N/A')}")
    print(f"Form Type: {['990', '990-EZ', '990-PF'][filing.get('formtype', 0)] if filing.get('formtype') is not None else 'N/A'}")
    if filing.get('pdf_url'):
        print(f"PDF URL: {filing['pdf_url']}")
    print(f"Last Updated: {filing.get('updated', 'N/A')}")
    print("---")

def main():
    # Example usage with filters
    query = "music"
    state = "AZ"
    c_code = "3"
    min_revenue = 1000  # $1 million minimum revenue
    max_revenue = None # 1000000  # $10 million maximum revenue
    ntee = None  # Education NTEE category

    search_results = search_nonprofits(query, state, c_code, min_revenue, max_revenue, ntee)
    
    if search_results:
        print(f"Search Results (filtered by revenue ${min_revenue:,} - ${max_revenue} and NTEE category '{ntee}'):")
        for org in search_results.get('organizations', [])[:5]:  # Print first 5 results
            print_organization_info(org)
        
        # Get details for the first organization in the search results
        if search_results.get('organizations'):
            first_org_ein = search_results['organizations'][0].get('ein')
            if first_org_ein:
                org_details = get_organization_details(first_org_ein)
                
                if org_details:
                    print("\nDetailed Organization Information:")
                    org = org_details.get('organization', {})
                    print_organization_info(org)
                    
                    print("\nAll Filings Information:")
                    filings_with_data = org_details.get('filings_with_data', [])
                    if filings_with_data:
                        for filing in filings_with_data:
                            print_filing_info(filing)
                    else:
                        print("No filing data available.")
            else:
                print("No EIN found for the first organization in search results.")
        else:
            print("No organizations found matching the specified criteria.")
    
if __name__ == "__main__":
    main()