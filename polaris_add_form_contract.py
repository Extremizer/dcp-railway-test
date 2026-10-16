"""Read-only Polaris exact Add form proof. No network and no mutation."""
import re

def validate_form(oem, forms):
    if not isinstance(oem,str) or re.fullmatch('[A-Z0-9-]{4,40}',oem) is None:
        raise ValueError('OEM_UNVERIFIED')
    if not isinstance(forms,list):
        raise ValueError('ADD_FORM_UNVERIFIED')
    found=[f for f in forms if isinstance(f,dict) and f.get('id')=='add_'+oem]
    if len(found)!=1:
        raise ValueError('ADD_FORM_UNVERIFIED')
    form=found[0]
    expected={'id':'add_'+oem,'action':'/cart/addoemsinglepart','method':'POST','manf':'polaris_atv','sku':oem,'qty':'1'}
    if any(str(form.get(k,''))!=v for k,v in expected.items()):
        raise ValueError('ADD_FORM_UNVERIFIED')
    return {'manufacturer':'Polaris','item_category':'Parts','requested_oem':oem,'resolved_oem':oem,'exact_match':True}
