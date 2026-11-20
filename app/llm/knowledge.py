"""商品知识库（演示用）。

作用：Mock Provider 在**没有真实模型**时，用这份结构化知识生成"看起来像老手写的"
葡语素材；接真实模型时，这份知识会作为 few-shot 证据注入 Prompt。

注意：这里的参数是**演示样本**，不代表真实商品；生产中所有参数必须来自商品事实表，
并在 spec_consistency 里逐条溯源。
"""

from __future__ import annotations

import re
from typing import Any

PRODUCT_KB: dict[str, dict[str, Any]] = {
    "scooter": {
        "aliases": ["patinete", "scooter", "电动滑板车", "滑板车"],
        "pt_name": "Patinete Elétrico 500W Adulto",
        "category": "Patinetes Elétricos",
        "positioning": "Patinete de entrada com motor forte: para quem precisa subir ladeira no trajeto diário, não só andar no plano.",
        "selling_point": "Motor 500W sobe ladeira de 15° com adulto de 90 kg sem perder velocidade",
        "secondary_points": [
            "Bateria 48V 12Ah: 35 km de autonomia real medida em uso urbano",
            "Dobrável em 5 segundos, 18,5 kg, cabe no porta-malas",
            "Freio a disco dianteiro + suspensão dupla",
        ],
        "bullets": [
            "Motor 500W (pico 800W): sobe ladeira de até 15° carregando até 90 kg",
            "Bateria 48V 12Ah: até 35 km de autonomia real e recarga completa em 4h",
            "Freio a disco + suspensão dianteira e traseira: menos impacto em calçamento",
            "Dobrável em 5s e 18,5 kg: entra no porta-malas e no elevador",
            "Pneus 8,5\" tubeless: menos furo e mais aderência em piso molhado",
        ],
        "description_sections": [
            ("Por que este patinete sobe ladeira", "O motor de 500W entrega 800W de pico por até 10 segundos. Na prática, isso significa subir uma rampa de garagem ou ladeira de bairro sem descer para empurrar. Testado com piloto de 90 kg em ladeira de 15°."),
            ("Autonomia que dá para planejar", "Com bateria de 48V 12Ah, a autonomia real fica em torno de 35 km em modo médio. Recarga completa em 4 horas em tomada 110V ou 220V (bivolt)."),
            ("Segurança no dia a dia", "Freio a disco na roda dianteira, campainha, refletores laterais e farol LED. O sistema corta a aceleração ao acionar o freio."),
            ("O que vem na caixa", "1 patinete montado, 1 carregador bivolt, 2 chaves, manual em português e kit de ferramentas."),
        ],
        "package_list": ["1 patinete elétrico", "1 carregador bivolt 42V", "2 chaves de ignição", "1 manual em português", "1 kit de ferramentas"],
        "specs": {
            "Motor": "500W (pico 800W)",
            "Bateria": "48V 12Ah",
            "Autonomia": "35 km (modo médio)",
            "Velocidade máxima": "30 km/h",
            "Peso do produto": "18,5 kg",
            "Carga máxima": "90 kg",
            "Pneu": "8,5 polegadas tubeless",
            "Freio": "Disco dianteiro + elétrico traseiro",
            "Tempo de recarga": "4 horas",
        },
        "keywords": [
            "patinete elétrico",
            "patinete elétrico adulto",
            "patinete 500w",
            "patinete dobrável",
            "scooter elétrico",
            "patinete elétrico 48v",
            "patinete para adulto 90kg",
            "patinete elétrico potente",
            "patinete com suspensão",
        ],
        "negative_keywords": ["infantil", "brinquedo", "peças", "bateria avulsa"],
        "config": {"motor": ["350W", "500W"], "bateria": ["8Ah", "12Ah"], "pneu": ["8,5\""]},
        "nine_grid": [
            ("主图·白底正面 45°", "整车 45° 白底，占画面 85%，无文字", "Product photo of a black electric scooter on pure white background, 45-degree front view, studio lighting, no text, e-commerce main image"),
            ("核心卖点·爬坡", "30° 仰角，骑手上坡，叠加一行字「Sobe ladeira de 15°」", "Electric scooter climbing a 15-degree street slope with a 90kg adult rider, urban Brazilian street, dynamic angle, sunlight"),
            ("折叠状态", "折叠后放入汽车后备箱", "Folded electric scooter fitting into a car trunk, real usage scene, natural light"),
            ("仪表盘特写", "LED 仪表显示电量与速度", "Close-up of LED dashboard showing battery level and speed, macro photography"),
            ("刹车/轮胎特写", "盘刹与 tubeless 轮胎细节", "Macro shot of disc brake and tubeless tire tread, metal texture, studio light"),
            ("承重演示", "90kg 骑手站立，标注「Suporta 90 kg」", "Side view of adult rider on scooter, weight capacity demonstration, clean background"),
            ("尺寸标注", "整车长宽高标注图", "Technical dimension diagram of electric scooter with measurement lines, white background"),
            ("使用场景·通勤", "早高峰街道，骑手通勤", "Rider commuting in São Paulo street at morning rush hour, lifestyle photography"),
            ("包装清单", "配件平铺图", "Flat lay of scooter accessories: charger, keys, manual, tool kit, white background"),
        ],
        "overlay_texts": [
            "", "Sobe ladeira 15°", "", "", "", "Suporta 90 kg", "", "", "Kit completo",
        ],
        "video": {
            "hook": "R$ 2.499 num patinete que não sobe ladeira? Esse sobe — com 90 kg em cima.",
            "shots": [
                (0, 3, "Rider parado no pé da ladeira, câmera baixa", "Sobe ladeira? Sobe. Com 90 kg em cima.", "Sobe ladeira de 15°"),
                (3, 8, "Rider subindo a ladeira, câmera acompanhando", "Motor 500W, pico de 800W. Sem descer para empurrar.", "Motor 500W"),
                (8, 14, "Detalhe do display e do freio", "35 km de autonomia e freio a disco nas duas rodas.", "35 km reais"),
                (14, 19, "Dobrando e colocando no porta-malas", "Dobra em 5 segundos e entra no porta-malas.", "Dobra em 5s"),
                (19, 24, "Produto na caixa, preço na tela", "Kit completo, nota fiscal e garantia de 12 meses.", "Nota fiscal + garantia"),
            ],
        },
    },
    "powerbank": {
        "aliases": ["power bank", "powerbank", "carregador portátil", "充电宝", "移动电源"],
        "pt_name": "Carregador Portátil 20.000 mAh 22,5W",
        "category": "Carregadores Portáteis",
        "positioning": "Bateria de bolso para quem passa o dia fora: carga rápida de verdade, capacidade real e aprovação para levar no avião.",
        "selling_point": "20.000 mAh com 22,5W: 4 cargas completas de celular em 1h30",
        "secondary_points": [
            "Saída USB-C bidirecional 22,5W (PD + QC 3.0)",
            "Display LED com porcentagem exata, sem adivinhar",
            "74 Wh: dentro do limite de 100 Wh para levar em voo",
        ],
        "bullets": [
            "20.000 mAh reais (74 Wh): cerca de 4 cargas completas de um celular de 4.500 mAh",
            "22,5W com PD + QC 3.0: 50% de bateria em 30 minutos",
            "USB-C bidirecional: carrega e é carregado pelo mesmo cabo",
            "Display LED com porcentagem: você sabe exatamente quanto sobrou",
            "6 proteções (curto, sobrecarga, temperatura): certificação CE/FCC/RoHS",
        ],
        "description_sections": [
            ("Capacidade que corresponde", "20.000 mAh a 3,7V equivalem a 74 Wh. Na prática, são cerca de 4 cargas completas de um celular de 4.500 mAh, com perda de conversão já descontada."),
            ("Carga rápida nos dois sentidos", "A porta USB-C trabalha em 22,5W nos dois sentidos: carrega o celular rápido e recarrega o power bank em 4h30 com carregador de 20W."),
            ("Pode levar no avião", "Com 74 Wh, está abaixo do limite de 100 Wh da ANAC para bagagem de mão. Leve na mochila, não no porão."),
            ("Na caixa", "1 carregador portátil, 1 cabo USB-C de 30 cm, 1 manual em português."),
        ],
        "package_list": ["1 carregador portátil 20.000 mAh", "1 cabo USB-C 30 cm", "1 manual em português"],
        "specs": {
            "Capacidade": "20.000 mAh / 74 Wh",
            "Saída máxima": "22,5W",
            "Portas": "2 USB-A + 1 USB-C",
            "Tecnologias": "PD 3.0 + QC 3.0",
            "Peso": "380 g",
            "Ciclos de carga": "500+",
            "Certificações": "CE / FCC / RoHS",
        },
        "keywords": [
            "carregador portátil",
            "power bank 20000mah",
            "carregador portátil 20000mah",
            "power bank carga rápida",
            "carregador celular portátil",
            "power bank usb c",
            "bateria externa celular",
        ],
        "negative_keywords": ["usado", "sem cabo", "defeito"],
        "config": {"capacidade": ["10.000 mAh", "20.000 mAh"], "cor": ["Preto", "Branco"]},
        "nine_grid": [
            ("主图·白底", "产品 45° 白底，占画面 85%", "Product photo of a black power bank on pure white background, 45-degree angle, studio lighting, no text"),
            ("容量对比", "旁边放手机对比大小", "Power bank next to a smartphone for size comparison, white background"),
            ("快充演示", "屏幕显示 50% 用时 30 分钟", "Phone screen showing 50 percent charge, power bank connected, clean desk scene"),
            ("接口特写", "USB-C 与双 USB-A 接口微距", "Macro shot of USB-C and dual USB-A ports, metal texture"),
            ("LED 显示屏", "电量百分比显示", "Close-up of LED percentage display on power bank"),
            ("随身携带", "放牛仔裤口袋/小包", "Power bank inside a small crossbody bag, lifestyle shot"),
            ("飞机场景", "登机牌 + 74Wh 标注", "Power bank with boarding pass on airport table, travel scene"),
            ("安全认证", "CE/FCC/RoHS 标识图示", "Certification badges CE FCC RoHS on clean white layout"),
            ("包装清单", "产品+数据线+说明书平铺", "Flat lay of power bank, USB-C cable and manual, white background"),
        ],
        "overlay_texts": ["", "", "50% em 30 min", "", "Bateria real", "", "Voo liberado", "", "Kit completo"],
        "video": {
            "hook": "Power bank que diz 20.000 e entrega 8.000? Esse entrega os 20.000.",
            "shots": [
                (0, 3, "Power bank na mão, celular em 5%", "20.000 mAh de verdade. Olha a conta.", "20.000 mAh reais"),
                (3, 8, "Celular carregando, cronômetro", "22,5W: 50% em 30 minutos.", "50% em 30 min"),
                (8, 13, "Display LED e portas", "Display de porcentagem e USB-C nos dois sentidos.", "USB-C bidirecional"),
                (13, 18, "Mochila e porta de embarque", "74 Wh: pode levar no avião.", "Aprovado para voo"),
                (18, 22, "Produto e cabo na caixa", "Cabo e manual inclusos. Nota fiscal.", "Kit completo"),
            ],
        },
    },
    "pet_carrier": {
        "aliases": ["pet carrier", "caixa de transporte", "宠物包", "宠物航空箱", "transportadora"],
        "pt_name": "Caixa de Transporte para Pets Tam. M",
        "category": "Caixas de Transporte para Pets",
        "positioning": "Transporte aprovado em companhias aéreas, com ventilação real e base firme: o pet viaja deitado, não apertado.",
        "selling_point": "Aprovada para cabine: 40×30×20 cm e suporta pets de até 8 kg",
        "secondary_points": [
            "Tela de ventilação em 3 lados, não só na frente",
            "Base rígida removível e lavável",
            "Trava de segurança dupla no zíper",
        ],
        "bullets": [
            "Medidas 40×30×20 cm: dentro do padrão de cabine da maioria das companhias",
            "Suporta até 8 kg com estrutura reforçada, sem deformar no colo",
            "Ventilação em 3 lados: circulação real, não só uma janelinha",
            "Base rígida removível e lavável: higiene depois do voo",
            "Zíper com trava dupla e alça acolchoada para transporte",
        ],
        "description_sections": [
            ("Medidas que passam na cabine", "40 cm de comprimento, 30 cm de largura e 20 cm de altura. Antes de viajar, confirme com a companhia: algumas exigem até 2 cm a menos."),
            ("Ventilação de verdade", "Tela mosquiteiro em três lados (frente, topo e laterais), o que mantém o ar circulando mesmo com o pet deitado."),
            ("Estrutura e higiene", "Base rígida removível, forro lavável e costura reforçada nos cantos — os pontos que costumam abrir primeiro."),
            ("Na caixa", "1 caixa de transporte, 1 forro removível, 1 manual."),
        ],
        "package_list": ["1 caixa de transporte M", "1 forro removível", "1 manual em português"],
        "specs": {
            "Medidas externas": "40 × 30 × 20 cm",
            "Peso suportado": "até 8 kg",
            "Material": "Oxford 600D impermeável",
            "Ventilação": "3 lados em tela",
            "Peso do produto": "900 g",
            "Base": "Rígida removível",
        },
        "keywords": [
            "caixa de transporte pet",
            "transportadora para gatos",
            "caixa de transporte cachorro",
            "pet carrier avião",
            "bolsa de transporte pet",
            "caixa pet tamanho m",
        ],
        "negative_keywords": ["usada", "sem zíper", "para peixe"],
        "config": {"tamanho": ["P", "M", "G"], "cor": ["Cinza", "Preto"]},
        "nine_grid": [
            ("主图·白底", "箱体 45° 白底", "Pet carrier bag on pure white background, 45-degree angle, studio lighting"),
            ("尺寸标注", "40×30×20 标注线", "Dimension diagram of pet carrier with measurement lines, 40x30x20 cm"),
            ("宠物在箱内", "幼猫在箱内探头", "Small cat sitting inside pet carrier, natural light, cozy"),
            ("通风特写", "三面网眼细节", "Close-up of mesh ventilation panels on pet carrier"),
            ("背带细节", "肩带与手提把手", "Close-up of padded shoulder strap and handle"),
            ("航空合规", "登机口场景", "Pet carrier at airport boarding gate, traveler with pet"),
            ("清洁演示", "取出可洗内衬", "Removing washable inner pad from pet carrier"),
            ("承重结构", "底部支撑结构图", "Cutaway diagram of rigid base structure"),
            ("包装清单", "箱体+内衬+说明书", "Flat lay of pet carrier, inner pad and manual"),
        ],
        "overlay_texts": ["", "40x30x20 cm", "", "", "", "Cabine ok", "Forro lavavel", "Base rigida", "Kit completo"],
        "video": {
            "hook": "Seu pet viaja apertado? Essa caixa tem 20 cm de altura — e passa na cabine.",
            "shots": [
                (0, 3, "Gato entrando na caixa", "Cabe? Cabe. E sobra espaço para deitar.", "40×30×20 cm"),
                (3, 8, "Medindo com trena", "Medidas dentro do padrão de cabine.", "Padrão de cabine"),
                (8, 13, "Mostrando ventilação nos 3 lados", "Ventilação em três lados, de verdade.", "Ventilação 3 lados"),
                (13, 18, "Retirando o forro", "Forro removível e lavável.", "Forro lavável"),
                (18, 22, "Pet confortável dentro", "Suporta até 8 kg. Nota fiscal e garantia.", "Até 8 kg"),
            ],
        },
    },
    "air_fryer": {
        "aliases": ["air fryer", "fritadeira", "空气炸锅", "fritadeira elétrica"],
        "pt_name": "Fritadeira Elétrica Air Fryer 5L",
        "category": "Fritadeiras sem Óleo",
        "positioning": "5 litros com cesto antiaderente que sai inteiro: cabe frango para a família e a limpeza leva 2 minutos.",
        "selling_point": "5 L com cesto removível: frango de 1,5 kg pronto em 25 min sem óleo",
        "secondary_points": [
            "8 programas com temperatura de 80 °C a 200 °C",
            "Cesto antiaderente removível, pode ir à lava-louças",
            "Desligamento automático e aviso sonoro",
        ],
        "bullets": [
            "5 L: cabem 1,5 kg de frango ou 800 g de batata por vez",
            "8 programas prontos (frango, batata, peixe, bolo, legumes, carne, camarão, reaquecer)",
            "Cesto antiaderente removível: lava na máquina e não solta o revestimento",
            "Temperatura de 80 °C a 200 °C com timer de 60 min",
            "Desligamento automático + aviso sonoro: sem risco de esquecer ligado",
        ],
        "description_sections": [
            ("Capacidade para família", "5 litros rendem 1,5 kg de frango ou 800 g de batata em uma única leva — o suficiente para 4 pessoas."),
            ("Antiaderente que dura", "Cesto e grelha com revestimento antiaderente removível. Não use esponja de aço; o revestimento é o que garante a soltura."),
            ("Controle de temperatura", "De 80 °C (para reaquecer) a 200 °C (para crocância), com timer de até 60 minutos e desligamento automático."),
            ("Na caixa", "1 air fryer 5 L, 1 cesto, 1 grelha, 1 manual em português com receitas."),
        ],
        "package_list": ["1 air fryer 5 L", "1 cesto antiaderente", "1 grelha removível", "1 manual com receitas"],
        "specs": {
            "Capacidade": "5 litros",
            "Potência": "1.500W",
            "Temperatura": "80 °C – 200 °C",
            "Timer": "até 60 min",
            "Voltagem": "110V / 220V",
            "Peso": "4,8 kg",
            "Programas": "8",
        },
        "keywords": [
            "air fryer",
            "fritadeira sem óleo",
            "air fryer 5l",
            "fritadeira elétrica",
            "air fryer grande",
            "fritadeira antiaderente",
        ],
        "negative_keywords": ["usada", "sem cesto", "peças"],
        "config": {"capacidade": ["3,5 L", "5 L", "6,5 L"], "voltagem": ["110V", "220V"]},
        "nine_grid": [
            ("主图·白底", "产品 45° 白底", "Air fryer on pure white background, 45-degree angle, studio lighting"),
            ("容量演示", "整鸡放入炸篮", "Whole chicken inside air fryer basket, top view"),
            ("成品特写", "金黄薯条与鸡翅", "Crispy golden french fries and chicken wings, food photography"),
            ("控制面板", "旋钮与程序图示", "Close-up of air fryer control panel with knobs"),
            ("可拆洗炸篮", "取出炸篮水洗", "Removing air fryer basket for washing under tap"),
            ("容量标注", "5L 标注图", "5 liter capacity diagram of air fryer, measurement overlay"),
            ("家庭场景", "厨房台面使用中", "Air fryer on modern kitchen counter, family cooking scene"),
            ("对比传统油炸", "用油量对比图", "Comparison chart of oil usage: air fryer vs deep fryer"),
            ("包装清单", "主机+炸篮+烤架+说明书", "Flat lay of air fryer, basket, rack and recipe book"),
        ],
        "overlay_texts": ["", "Cabe 1,5 kg", "", "", "Lava na maquina", "5 litros", "", "90% menos oleo", "Kit completo"],
        "video": {
            "hook": "1,5 kg de frango em 25 minutos. Sem óleo. Numa air fryer de 5 litros.",
            "shots": [
                (0, 3, "Frango cru temperado na bancada", "Frango para a família toda. Sem óleo.", "1,5 kg de frango"),
                (3, 8, "Colocando no cesto, fechando", "Cesto de 5 litros, antiaderente e removível.", "Cesto 5 L"),
                (8, 14, "Timer e temperatura", "200 °C por 25 minutos. Programa pronto.", "200 °C · 25 min"),
                (14, 19, "Abrindo, frango dourado", "Dourado por fora, suculento por dentro.", "Sem óleo"),
                (19, 23, "Lavando o cesto", "Cesto na lava-louças. Limpeza em 2 minutos.", "Lava na máquina"),
            ],
        },
    },
    "thermal_bag": {
        "aliases": ["thermal bag", "bolsa térmica", "保温箱", "保冷包", "delivery bag"],
        "pt_name": "Bolsa Térmica 32L para Delivery",
        "category": "Bolsas Térmicas",
        "positioning": "Bolsa de delivery com isolamento de 8 horas: mantém congelados firmes na última entrega do turno.",
        "selling_point": "32 L com 8h de isolamento: mantém -18 °C do primeiro ao último pedido",
        "secondary_points": [
            "Parede dupla com isolamento de 10 mm",
            "Fundo reforçado: 4 pedidos por viagem",
            "Zíper reforçado e alça que não rasga no uso diário",
        ],
        "bullets": [
            "32 L: cabem 4 pedidos grandes por viagem",
            "8 horas de isolamento com gelo seco ou placas de gel",
            "Parede dupla de 10 mm: barreira térmica real, não plástico fino",
            "Fundo reforçado e costura dupla nos pontos de esforço",
            "Dobrável: ocupa pouco espaço quando não está em uso",
        ],
        "description_sections": [
            ("Isolamento medido", "Parede dupla com 10 mm de isolamento. Com 2 placas de gel, mantém produtos congelados por até 8 horas em temperatura ambiente de 30 °C."),
            ("Capacidade por viagem", "32 litros comportam 4 pedidos grandes ou 6 médios. O fundo reforçado evita que a bolsa ceda com peso."),
            ("Uso diário", "Zíper reforçado, alça costurada com fita de poliéster e reforço nos cantos — os pontos que rasgam primeiro em bolsa barata."),
            ("Na caixa", "1 bolsa térmica 32 L, 2 placas de gel reutilizáveis."),
        ],
        "package_list": ["1 bolsa térmica 32 L", "2 placas de gel", "1 manual"],
        "specs": {
            "Capacidade": "32 litros",
            "Isolamento": "8 horas",
            "Espessura da parede": "10 mm",
            "Material externo": "Poliéster 600D",
            "Peso": "1,2 kg",
            "Fechamento": "Zíper reforçado",
        },
        "keywords": [
            "bolsa térmica",
            "bolsa térmica delivery",
            "bolsa térmica 32l",
            "bolsa térmica grande",
            "bolsa para entrega",
            "cooler bag",
        ],
        "negative_keywords": ["usada", "sem zíper", "pequena"],
        "config": {"capacidade": ["20 L", "32 L"], "cor": ["Preto", "Azul"]},
        "nine_grid": [
            ("主图·白底", "包体 45° 白底", "Thermal delivery bag on pure white background, 45-degree angle, studio lighting"),
            ("容量演示", "装入 4 个外卖盒", "Four food containers inside thermal bag, top view"),
            ("保温演示", "温度计显示 -18°C", "Thermometer inside thermal bag showing minus 18 degrees"),
            ("内衬特写", "铝箔内衬反光质感", "Close-up of reflective aluminum inner lining"),
            ("加固细节", "拉链与缝线微距", "Macro shot of reinforced zipper and stitching"),
            ("外卖场景", "骑手取餐装包", "Delivery rider loading food into thermal bag, street scene"),
            ("折叠状态", "折叠后平放", "Folded thermal bag lying flat, space saving"),
            ("尺寸标注", "长宽高标注", "Dimension diagram of thermal bag with measurement lines"),
            ("包装清单", "包体+冰袋", "Flat lay of thermal bag and two gel packs"),
        ],
        "overlay_texts": ["", "Cabe 4 pedidos", "-18C por 8h", "", "", "", "Dobra e guarda", "", "Kit completo"],
        "video": {
            "hook": "Última entrega do turno chega derretida? Essa bolsa segura -18 °C por 8 horas.",
            "shots": [
                (0, 3, "Pedido saindo do freezer", "Saiu do freezer às 11h. Chega às 19h.", "-18 °C por 8h"),
                (3, 8, "Colocando pedidos na bolsa", "32 litros: 4 pedidos por viagem.", "32 litros"),
                (8, 13, "Termômetro dentro da bolsa", "Parede dupla de 10 mm com placas de gel.", "Parede dupla 10 mm"),
                (13, 18, "Rua, moto, entrega", "Zíper e alça reforçados para uso diário.", "Reforçada"),
                (18, 22, "Abrindo na porta do cliente", "Chega firme. Nota fiscal e garantia.", "Kit completo"),
            ],
        },
    },
}


def _normalize(text: str) -> str:
    return re.sub(r"[\s\-_/]+", " ", (text or "").lower()).strip()


def find_kb(product: str, category: str = "") -> tuple[str, dict[str, Any]]:
    """按商品名/类目匹配知识库，返回 (key, kb)；未命中返回 ("generic", {})。"""
    haystack = _normalize(f"{product} {category}")
    for key, kb in PRODUCT_KB.items():
        for alias in kb["aliases"]:
            if _normalize(alias) in haystack:
                return key, kb
    for key, kb in PRODUCT_KB.items():
        if _normalize(kb["pt_name"]) in haystack:
            return key, kb
    return "generic", {}


def generic_kb(product: str, category: str = "") -> dict[str, Any]:
    """通用兜底：没有知识库时仍然产出合规、可用的素材（不编造参数）。"""
    name = product or "Produto"
    return {
        "pt_name": name,
        "category": category or "Acessórios",
        "positioning": f"{name}: produto de uso diário com especificação clara e sem promessa exagerada.",
        "selling_point": f"{name} com especificação completa e envio rápido",
        "secondary_points": [
            "Ficha técnica completa, sem dado inventado",
            "Embalagem reforçada para transporte",
            "Nota fiscal e garantia",
        ],
        "bullets": [
            f"{name} com as especificações descritas na ficha técnica",
            "Produto conferido antes do envio",
            "Embalagem reforçada para evitar avaria no transporte",
            "Nota fiscal emitida em todas as compras",
            "Suporte em português pelo chat da loja",
        ],
        "description_sections": [
            ("Sobre o produto", f"{name} — confira as medidas e a voltagem na ficha técnica antes de comprar."),
            ("O que vem na caixa", "1 produto, 1 manual e os acessórios descritos na ficha técnica."),
        ],
        "package_list": ["1 produto", "1 manual"],
        "specs": {},
        "keywords": [name.lower()],
        "negative_keywords": ["usado", "defeito"],
        "config": {},
        "nine_grid": [
            ("主图·白底", "产品 45° 白底，主体占比 85%", "Product photo on pure white background, 45-degree angle, studio lighting, no text"),
            ("卖点图", "核心卖点一行字", "Product hero shot with one-line benefit overlay, clean background"),
            ("尺寸标注", "长宽高标注", "Dimension diagram with measurement lines, white background"),
            ("细节特写", "材质/做工微距", "Macro shot of product material and finish"),
            ("使用场景", "真实使用环境", "Product in real usage scenario, natural light"),
            ("对比图", "与常见替代品对比", "Side by side comparison with alternative product"),
            ("结构说明", "剖面/结构图", "Cutaway diagram explaining product structure"),
            ("包装展示", "彩盒正面", "Product retail packaging, front view"),
            ("包装清单", "配件平铺", "Flat lay of product and all included accessories"),
        ],
        "overlay_texts": ["", "", "", "", "", "", "", "", "Kit completo"],
        "video": {
            "hook": f"Antes de comprar {name}, olha o que vem na caixa.",
            "shots": [
                (0, 3, "Produto em destaque", f"{name}: o básico bem feito.", "Ficha técnica completa"),
                (3, 8, "Detalhes do produto", "Material e acabamento em close.", "Detalhes"),
                (8, 13, "Uso no dia a dia", "Uso real, sem promessa exagerada.", "Uso diário"),
                (13, 18, "Medidas na tela", "Confira as medidas antes de comprar.", "Confira as medidas"),
                (18, 22, "Produto e acessórios", "Nota fiscal e garantia.", "Kit completo"),
            ],
        },
    }
