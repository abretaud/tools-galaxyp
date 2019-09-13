"""
Create a project-specific MaxQuant parameter file.

TODO: add reporter ion MS2

Author: Damian Glaetzer <d.glaetzer@mailbox.org>
"""

import copy
import ntpath
import os
import re
import xml.etree.ElementTree as ET
from itertools import zip_longest
from xml.dom import minidom

def et_add_child(el, name, text, attrib=None):
    "Add a child element to an xml.etree.ElementTree.Element"
    child = ET.SubElement(el, name, attrib=attrib if attrib else {})
    child.text = str(text)

    return child


class ParamGroup:
    """Represents one parameter Group
    """

    def __init__(self, root):
        """Initialize with its xml.etree.ElementTree root Element.
        """
        self._root = copy.deepcopy(root)
        
        
    def set_list_params(self, key, vals):
        """Set a list parameter.
        """
        
        # params = 'variableModifications','fixedModifications','enzymes'
        
        node = self._root.find(key)
        if node is None:
            raise ValueError('Element {} not found in parameter file'
                             .format(key))
        node.clear()
        node.tag = key
        for e in vals:
            et_add_child(node, name='string', text=e)
                 
    def set_simple_param(self, key, value):
        """Set a simple parameter.
        """
        node = self._root.find(key)
        if node is None:
            raise ValueError('Element {} not found in parameter file'
                             .format(key))
        node.text = str(value)

    def set_silac(self, light_mods, medium_mods, heavy_mods):
        """Set label modifications.
        """
        multiplicity = 3 if medium_mods else 2 if heavy_mods else 1
        max_label = str(max(len(light_mods) if light_mods else 0,
                            len(medium_mods) if medium_mods else 0,
                            len(heavy_mods) if heavy_mods else 0))
        self._root.find('multiplicity').text = str(multiplicity)
        self._root.find('maxLabeledAa').text = max_label

        node = self._root.find('labelMods')
        node[0].text = ';'.join(light_mods) if light_mods else ''
        if multiplicity == 3:
            et_add_child(node, name='string', text=';'.join(medium_mods))
        if multiplicity > 1:
            et_add_child(node, name='string',
                               text=';'.join(heavy_mods) if heavy_mods else '')

    def set_isobaric_label(self, internalLabel, terminalLabel, corrections, tmtLike):
        """Add isobaric label info.
        Args:
            internalLabel: string
            terminalLabel: string
            corrections: iterable of floats, length 4
            tmtLike: bool or string
        Returns:
            None
        """
        
        iso_labels_node = self._root.find('isobaricLabels')
        label = et_add_child(iso_labels_node, 'IsobaricLabelInfo', '')
        et_add_child(label, 'internalLabel', internalLabel)
        et_add_child(label, 'terminalLabel', terminalLabel)
        for num, factor in zip(['M2', 'M1', 'P1', 'P2'], corrections):
            et_add_child(label, 'correctionFactor' + num, str(factor))
        et_add_child(label, 'tmtLike', str(tmtLike))

class MQParam:
    """Represents a mqpar.xml and provides methods to modify
    some of its parameters.
    """

    fasta_template = """<FastaFileInfo>
    <fastaFilePath></fastaFilePath>
    <identifierParseRule></identifierParseRule>
    <descriptionParseRule></descriptionParseRule>
    <taxonomyParseRule></taxonomyParseRule>
    <variationParseRule></variationParseRule>
    <modificationParseRule></modificationParseRule>
    <taxonomyId></taxonomyId>
    </FastaFileInfo>"""

    def __init__(self, mqpar_out, mqpar_in, exp_design,
                 substitution_rx=r'[^\s\S]'):  # no sub by default
        """Initialize MQParam class. mqpar_in can either be a template
        or a already suitable mqpar file.

        Args:
            mqpar_out: the file to write the resulting paramter file in

            mqpar_in: a template parameter file

            exp_design: a experimental design template (see MaxQuant documentation),
            can be None

            substitution_rx: a regular expression for replacements in the file names.
            It is applied before comparing input file names (e.g. from the exp. design)
        """

        self.orig_mqpar = mqpar_in
        self.exp_design = exp_design
        self.mqpar_out = mqpar_out
        self._root = ET.parse(mqpar_in).getroot()
        self.version = self._root.find('maxQuantVersion').text
        # regex for substitution of certain file name characters
        self.substitution_rx = substitution_rx
        self._paramGroups = []

    def __getitem__(self, index):
        return self._paramGroups[index]

    @staticmethod
    def _check_validity(design, len_infiles):
        """Perform some checks on the exp. design template"""
        design_len = len(design['Name'])
        match = len(list(filter(lambda x: bool(x), design['Name'])))
        if match < len_infiles:
            raise Exception("Error parsing experimental design template: " +
                            "Found only {} matching entries ".format(match) +
                            "for {} input files".format(len_infiles))
        for i in range(0, design_len):
            msg = "(in line " + str(i + 2) + " of experimental design) "
            if not design['Experiment'][i]:
                raise ValueError(msg + " Experiment is empty.")
            if design['PTM'][i].lower() not in ('true', 'false'):
                raise ValueError(msg + "Defines invalid PTM value, " +
                                "should be 'True' or 'False'.")
            try:
                int(design['Fraction'][i])
            except ValueError as e:
                raise ValueError(msg + str(e))

    def _make_exp_design(self, groups, files):
        """Create a dict representing an experimental design from an
        experimental design template and a list input files.
        If the experimental design template is None, create a default 
        design with one experiment for each input file and no fractions
        for all files.

        Args:
            files: list of input file paths

            groups: list of parameter group indices

        Returns:
            dict: The (complete) experimental design template
        """

        design = {s: [] for s in ("Name", "PTM", "Fraction", "Experiment", "paramGroup")}
        if not self.exp_design:
            design["Name"] = files
            design["Fraction"] = ('32767',) * len(files)
            design["Experiment"] = [os.path.split(f)[1] for f in files]
            design["PTM"] = ('False',) * len(files)
            design["paramGroup"] = groups
        else:
            with open(self.exp_design) as design_file:
                index_line = design_file.readline().strip()
                index = []
                for i in index_line.split('\t'):
                    if i in design:
                        index.append(i)
                    else:
                        raise Exception("Invalid column index in experimental"
                                        + " design template: {}".format(i))

                for line in design_file:
                    row = line.strip().split('\t')
                    for e, i in zip_longest(row, index):
                        if i == "Fraction" and not e:
                            e = '32767'
                        elif i == "PTM" and not e:
                            e = 'False'
                        design[i].append(e)

            # map files to names in exp. design template
            names = []
            names_to_paths = {}
            # strip path and extension
            for f in files:
                b = os.path.basename(f)
                basename = b[:-6] if b.endswith('.mzXML') else b[:-11]
                names_to_paths[basename] = f
            for name in design['Name']:
                # same substitution as in maxquant.xml,
                # when passing the element identifiers
                fname = re.sub(self.substitution_rx, '_', name)
                names.append(names_to_paths[fname] if fname in names_to_paths
                             else None)
            # replace orig. file names with matching links to galaxy datasets
            design['Name'] = names
            design['paramGroup'] = groups
            MQParam._check_validity(design, len(files))

        return design

    def add_infiles(self, infiles):
        """Add a list of raw/mzxml files to the mqpar.xml.
        If experimental design template was specified,
        modify other parameters accordingly.
        The files must be specified as absolute paths
        for maxquant to find them.
        Also add parameter Groups.

        Args:
            infiles: a list of infile lists. first dimension denotes the
            parameter group.

        Returns:
            None
        """
        
        groups, files = zip(*[(num, f) for num, l in enumerate(infiles) for f in l])
        pg_node = self._root.find('parameterGroup')
        self._paramGroups = [ParamGroup(pg_node) for i in range(len(infiles))]

        nodenames = ('filePaths', 'experiments', 'fractions',
                     'ptms', 'paramGroupIndices', 'referenceChannel')
        design = self._make_exp_design(groups, files)

        # Get parent nodes from document
        nodes = dict()
        for nodename in nodenames:
            node = self._root.find(nodename)
            if node is None:
                raise ValueError('Element {} not found in parameter file'
                                 .format(nodename))
            nodes[nodename] = node
            node.clear()
            node.tag = nodename

        # Append sub-elements to nodes (one per file)
        for i, name in enumerate(design['Name']):
            if name:
                et_add_child(nodes['filePaths'], 'string', name)
                et_add_child(nodes['experiments'], 'string',
                             design['Experiment'][i])
                et_add_child(nodes['fractions'], 'short',
                             design['Fraction'][i])
                et_add_child(nodes['ptms'], 'boolean',
                             design['PTM'][i])
                et_add_child(nodes['paramGroupIndices'], 'int',
                             design['paramGroup'][i])
                et_add_child(nodes['referenceChannel'], 'string', '')

    def translate(self, infiles):
        """Map a list of given infiles to the files specified in the parameter file.
        Needed for the mqpar upload in galaxy. Removes the path and then tries
        to match the files.

        Args:
            infiles: list or tuple of the input files

        Returns:
            None
        """
        
        # kind of a BUG: fails if filename starts with '.'
        infilenames = [os.path.basename(f).split('.')[0] for f in infiles]
        filesNode = self._root.find('filePaths')
        files_from_mqpar = [e.text for e in filesNode]
        filesNode.clear()
        filesNode.tag = 'filePaths'
        for f in files_from_mqpar:
            # either windows or posix path
            win = ntpath.basename(f)
            posix = os.path.basename(f)
            basename = win if len(win) < len(posix) else posix
            basename_with_sub = re.sub(self.substitution_rx, '_',
                                       basename.split('.')[0])
            # match infiles to their names in mqpar.xml,
            # ignore files missing in mqpar.xml
            if basename_with_sub in infilenames:
                i = infilenames.index(basename_with_sub)
                et_add_child(filesNode, 'string', infiles[i])
            else:
                raise ValueError("no matching infile found for " + f)

    def add_fasta_files(self, files, identifier=r'>([^\s]*)', description=r'>(.*)'):
        """Add fasta file groups.
        Args:
            files: List of fasta file paths
            identifier: string, perl(?) regex to parse identifier
            description: string, perl(?) regex to parse description

        Returns:
            None
        """
        fasta_node = self._root.find("fastaFiles")
        fasta_node.clear()
        fasta_node.tag = "fastaFiles"

        for index in range(len(files)):
            filepath = '<fastaFilePath>' + files[index]
            identifier = identifier.replace('<', '&lt;')
            description = description.replace('<', '&lt;')
            fasta = self.fasta_template.replace('<fastaFilePath>', filepath)
            fasta = fasta.replace('<identifierParseRule>',
                                  '<identifierParseRule>' + identifier)
            fasta = fasta.replace('<descriptionParseRule>',
                                  '<descriptionParseRule>' + description)
            ff_node = self._root.find('.fastaFiles')
            fastaentry = ET.fromstring(fasta)
            ff_node.append(fastaentry)

    def set_simple_param(self, key, value):
        """Set a simple parameter.
        """
        node = self._root.find(key)
        if node is None:
            raise ValueError('Element {} not found in parameter file'
                             .format(key))
        node.text = str(value)

    def write(self):
        """Write pretty formatted xml parameter file.
        Compose it from global parameters and parameter Groups.
        """
        if self._paramGroups:
            template_pg = self._root.find('.ParamGroups/ParamGroup')
            pg_node = self._root.find('ParamGroups')
            pg_node.remove(template_pg)
            for group in self.ParamGroups:
                pg_node.append(group)
            
        rough_string = ET.tostring(self._root, 'utf-8', short_empty_elements=False)
        reparsed = minidom.parseString(rough_string)
        pretty = reparsed.toprettyxml(indent="\t")
        even_prettier = re.sub(r"\n\s+\n", r"\n", pretty)
        with open(self.mqpar_out, 'w') as f:
            print(even_prettier, file=f)