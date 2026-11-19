from functools import lru_cache

from rdkit import Chem, RDLogger
from rdkit.Chem import rdChemReactions
from rdkit.Chem.Draw import rdMolDraw2D

from .config import FONT_ROOT

RDLogger.DisableLog("rdApp.warning")
RDLogger.DisableLog("rdApp.error")


def canonical(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 1000:
        raise ValueError("Enter a single-molecule SMILES string (up to 1,000 characters).")
    mol = Chem.MolFromSmiles(text.strip())
    if mol is None or len(Chem.GetMolFrags(mol)) != 1:
        raise ValueError(
            "SMILES could not be read as one connected molecule. Salts and mixtures are outside this prototype."
        )
    if mol.GetNumHeavyAtoms() > 65 or mol.GetNumHeavyAtoms() < 2:
        raise ValueError("Use a molecule with 2–65 non-hydrogen atoms for this prototype.")
    for atom in mol.GetAtoms():
        atom.SetAtomMapNum(0)
        if atom.GetNumRadicalElectrons():
            raise ValueError("Radicals are outside this prototype.")
    return Chem.MolToSmiles(mol, isomericSmiles=True)


def svg(smiles, width=380, height=210):
    drawer = rdMolDraw2D.MolDraw2DSVG(width, height)
    drawer.drawOptions().clearBackground = False
    drawer.drawOptions().useBWAtomPalette()
    drawer.drawOptions().fontFile = str(FONT_ROOT / "SourceCodePro-Regular.ttf")
    rdMolDraw2D.PrepareAndDrawMolecule(drawer, Chem.MolFromSmiles(smiles))
    drawer.FinishDrawing()
    return drawer.GetDrawingText()


@lru_cache(maxsize=20000)
def apply_rule(smiles, retro, forward):
    """Generate graph rewrites and require exact forward structural reconstruction."""
    mol = Chem.MolFromSmiles(smiles)
    reverse = rdChemReactions.ReactionFromSmarts(retro)
    fwd = rdChemReactions.ReactionFromSmarts(forward)
    if reverse is None or fwd is None:
        return (), False
    products = reverse.RunReactants((mol,), maxProducts=256)
    capped = len(products) >= 256
    seen, valid = set(), []
    for fragments in products:
        try:
            parts = []
            for fragment in fragments:
                Chem.SanitizeMol(fragment)
                parts.append(canonical(Chem.MolToSmiles(fragment, isomericSmiles=True)))
            # Intramolecular transformations require different stoichiometry; exclude in v1.
            if len(parts) != fwd.GetNumReactantTemplates() or smiles in parts:
                continue
            outcomes = fwd.RunReactants(
                tuple(Chem.MolFromSmiles(p) for p in parts), maxProducts=256
            )
            capped = capped or len(outcomes) >= 256
            matched = False
            for outcome in outcomes:
                for p in outcome:
                    try:
                        Chem.SanitizeMol(p)
                        if canonical(Chem.MolToSmiles(p, isomericSmiles=True)) == smiles:
                            matched = True
                            break
                    except (ValueError, RuntimeError):
                        pass
                if matched:
                    break
            signature = tuple(sorted(parts))
            if matched and signature not in seen:
                seen.add(signature)
                valid.append(tuple(parts))
        except (ValueError, RuntimeError):
            continue
    return tuple(valid), capped
