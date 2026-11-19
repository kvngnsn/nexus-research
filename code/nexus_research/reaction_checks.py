"""Paired graph checks for AI proposals; no route search or economic assumptions.

Passing these checks establishes graph consistency only, not reaction feasibility.
Targets are not limited to these reaction classes; unsupported proposals remain unverified.
"""

RULES = [
    {
        "id": "ester_acid",
        "name": "Ester from acid + alcohol",
        "retro": "[C:1](=[O:2])[O:3][C;X4:4]>>[C:1](=[O:2])O.[OH:3][C:4]",
        "forward": "[C:1](=[O:2])[O;H1].[O;H1:3][C;X4:4]>>[C:1](=[O:2])[O:3][C:4]",
    },
    {
        "id": "ester_chloride",
        "name": "Ester from acyl chloride + alcohol",
        "retro": "[C:1](=[O:2])[O:3][$([C;X4]),$([c]):4]>>[C:1](=[O:2])Cl.[OH:3][#6:4]",
        "forward": "[C:1](=[O:2])Cl.[O;H1:3][$([C;X4]),$([c]):4]>>[C:1](=[O:2])[O:3][#6:4]",
    },
    {
        "id": "acetate_anhydride",
        "name": "Acetate from anhydride + alcohol",
        "retro": "[CH3:5][C:1](=[O:2])[O:3][$([C;X4]),$([c]):4]>>[CH3:5][C:1](=[O:2])OC(C)=O.[OH:3][#6:4]",
        "forward": "[CH3:5][C:1](=[O:2])OC(C)=O.[O;H1:3][$([C;X4]),$([c]):4]>>[CH3:5][C:1](=[O:2])[O:3][#6:4]",
    },
    {
        "id": "anhydride_substitution",
        "name": "Anhydride from acyl chloride + acid",
        "retro": "[C:1](=[O:2])[O:3][C:4](=[O:5])>>[C:1](=[O:2])Cl.[OH:3][C:4](=[O:5])",
        "forward": "[C:1](=[O:2])Cl.[O;H1:3][C:4](=[O:5])>>[C:1](=[O:2])[O:3][C:4](=[O:5])",
    },
    {
        "id": "amide_chloride",
        "name": "Amide from acyl chloride + amine",
        "retro": "[C:1](=[O:2])[NH:3][#6:4]>>[C:1](=[O:2])Cl.[NH2:3][#6:4]",
        "forward": "[C:1](=[O:2])Cl.[N;H2:3][#6:4]>>[C:1](=[O:2])[NH:3][#6:4]",
    },
    {
        "id": "amide_coupling",
        "name": "Amide from acid + amine (coupling)",
        "retro": "[C:1](=[O:2])[NH:3][#6:4]>>[C:1](=[O:2])O.[NH2:3][#6:4]",
        "forward": "[C:1](=[O:2])[O;H1].[N;H2:3][#6:4]>>[C:1](=[O:2])[NH:3][#6:4]",
    },
    {
        "id": "acid_hydrolysis",
        "name": "Carboxylic acid from methyl ester",
        "retro": "[C:1](=[O:2])[OH:3]>>[C:1](=[O:2])[O:3]C",
        "forward": "[C:1](=[O:2])[O:3][CH3]>>[C:1](=[O:2])[OH:3]",
    },
    {
        "id": "acid_oxidation",
        "name": "Carboxylic acid from aldehyde",
        "retro": "[#6:4][C:1](=[O:2])[OH]>>[#6:4][CH:1]=[O:2]",
        "forward": "[#6:4][CH:1]=[O:2]>>[#6:4][C:1](=[O:2])O",
    },
    {
        "id": "aldehyde_oxidation",
        "name": "Aldehyde from primary alcohol",
        "retro": "[#6:3][CH:1]=[O:2]>>[#6:3][CH2:1][OH:2]",
        "forward": "[#6:3][CH2:1][O;H1:2]>>[#6:3][CH:1]=[O:2]",
    },
    {
        "id": "alcohol_reduction",
        "name": "Secondary alcohol from ketone",
        "retro": "[#6:3][CH:1]([OH:2])[#6:4]>>[#6:3][C:1](=[O:2])[#6:4]",
        "forward": "[#6:3][C:1](=[O:2])[#6:4]>>[#6:3][CH:1]([OH:2])[#6:4]",
    },
    {
        "id": "ketone_oxidation",
        "name": "Ketone from secondary alcohol",
        "retro": "[#6:3][C:1](=[O:2])[#6:4]>>[#6:3][CH:1]([OH:2])[#6:4]",
        "forward": "[#6:3][CH:1]([O;H1:2])[#6:4]>>[#6:3][C:1](=[O:2])[#6:4]",
    },
    {
        "id": "aryl_ether",
        "name": "Aryl ether from phenol + primary alkyl bromide",
        "retro": "[c:1][O:2][C;H2,H3:3]>>[c:1][OH:2].[C:3]Br",
        "forward": "[c:1][O;H1:2].[C;H2,H3:3]Br>>[c:1][O:2][C:3]",
    },
    {
        "id": "biaryl_coupling",
        "name": "Biaryl from aryl bromide + boronic acid",
        "retro": "[c:1]-[c:2]>>[c:1]Br.[c:2]B(O)O",
        "forward": "[c:1]Br.[c:2]B(O)O>>[c:1]-[c:2]",
    },
]
