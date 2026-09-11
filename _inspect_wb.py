import requests
from bs4 import BeautifulSoup
import json

url = 'https://www.wbuscatti.com.br/categoria-produto/pulseiras/couro/'
html = requests.get(url, headers={'User-Agent':'Mozilla/5.0'}, timeout=30).text
soup = BeautifulSoup(html, 'lxml')
print('title:', soup.title.text.strip() if soup.title else '')
print('li.product:', len(soup.select('li.product')))
print('article.product:', len(soup.select('article.product')))
print('ld+json scripts:', len(soup.select("script[type='application/ld+json']")))
for i,sc in enumerate(soup.select("script[type='application/ld+json']")[:5],1):
    txt = sc.get_text(strip=True)
    print(f'ldjson[{i}] head:', txt[:180])

# print common class names that include "product"
classes = {}
for tag in soup.find_all(True):
    for c in tag.get('class', []):
        if 'product' in c.lower():
            classes[c] = classes.get(c, 0) + 1
print('classes with product (top 20):')
for k,v in sorted(classes.items(), key=lambda x: -x[1])[:20]:
    print(' ',k,v)
