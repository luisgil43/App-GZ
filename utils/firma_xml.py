# utils/firma_xml.py
from signxml import XMLSigner
from lxml import etree


def firmar_xml_documento(xml_root, private_key, cert):
    doc = xml_root.find('.//{http://www.sii.cl/SiiDte}Documento')
    signer = XMLSigner(method="enveloped", digest_algorithm="sha1",
                       c14n_algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315")
    signed_doc = signer.sign(doc, key=private_key, cert=cert)
    doc.getparent().replace(doc, signed_doc)
    return xml_root


def firmar_xml_setdte(xml_root, private_key, cert):
    setdte = xml_root.find('.//{http://www.sii.cl/SiiDte}SetDTE')
    signer = XMLSigner(method="enveloped", digest_algorithm="sha1",
                       c14n_algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315")
    signed_setdte = signer.sign(setdte, key=private_key, cert=cert)
    setdte.getparent().replace(setdte, signed_setdte)
    return xml_root
