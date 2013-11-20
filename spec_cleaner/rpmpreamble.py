# vim: set ts=4 sw=4 et: coding=UTF-8

import re

from rpmsection import Section
from fileutils import FileUtils
from rpmexception import RpmException


LICENSES_CHANGES = 'licenses_changes.txt'
PKGCONFIG_CONVERSIONS = 'pkgconfig_conversions.txt'


class RpmPreamble(Section):
    """
        Only keep one empty line for many consecutive ones.
        Reorder lines.
        Fix bad licenses.
        Use one line per BuildRequires/Requires/etc.
        Use %{version} instead of %{version}-%{release} for BuildRequires/etc.
        Standardize BuildRoot.

        This one is a bit tricky since we reorder things. We have a notion of
        paragraphs, categories, and groups.

        A paragraph is a list of non-empty lines. Conditional directives like
        %if/%else/%endif also mark paragraphs. It contains categories.
        A category is a list of lines on the same topic. It contains a list of
        groups.
        A group is a list of lines where the first few ones are either %define
        or comment lines, and the last one is a normal line.

        This means that comments will stay attached to one
        line, even if we reorder the lines.
    """


    category_to_key = {
        'define': '%define',
        'name': 'Name',
        'version': 'Version',
        'release': 'Release',
        'license': 'License',
        'summary': 'Summary',
        # The localized summary can contain various values, so it can't be here
        'url': 'Url',
        'group': 'Group',
        'source': 'Source',
        'patch': 'Patch',
        'buildrequires': 'BuildRequires',
        'prereq': 'PreReq',
        'requires': 'Requires',
        'recommends': 'Recommends',
        'suggests': 'Suggests',
        'supplements': 'Supplements',
        # Provides/Obsoletes cannot be part of this since we want to keep them
        # mixed, so we'll have to specify the key when needed
        'buildroot': 'BuildRoot',
        'buildarch': 'BuildArch',
        'epoch': 'Epoch'
    }

    categories_order = [
        'define',
        'name',
        'version',
        'release',
        'license',
        'summary',
        'summary_localized',
        'url',
        'group',
        'source',
        'patch',
        'buildrequires',
        'requires',
        'prereq',
        'requires_phase', # this is Requires(pre/post/...)
        'recommends',
        'suggests',
        'supplements',
        'provides_obsoletes',
        'buildroot',
        'buildarch',
        'misc',
    ]

    # categories that are sorted based on value in them
    categories_with_sorted_package_tokens = [
        'buildrequires',
        'prereq',
        'requires',
        'recommends',
        'suggests',
        'supplements',
    ]

    # categories that are sorted based on key value (eg Patch0 before Patch1)
    categories_with_sorted_keyword_tokens = [
        'source',
        'patch',
    ]


    def __init__(self, specfile):
        Section.__init__(self, specfile)
        # dict of license replacement options
        self.license_conversions = self._read_licenses_changes()
        # dict of pkgconfig conversions
        self.pkgconfig_conversions = self._read_pkgconfig_changes()
        # start the object
        self._start_paragraph()
        # initialize list of groups that need to pass over conversion fixer
        self.categories_with_package_tokens = self.categories_with_sorted_package_tokens[:]
        # these packages actually need fixing after we sent the values to reorder them
        self.categories_with_package_tokens.append('provides_obsoletes')

        # simple categories matching
        self.category_to_re = {
            'define': self.reg.re_define,
            'name': self.reg.re_name,
            'version': self.reg.re_version,
            # license need fix replacment
            'summary': self.reg.re_summary,
            'url': self.reg.re_url,
            'group': self.reg.re_group,
            # for source, we have a special match to keep the source number
            # for patch, we have a special match to keep the patch number
            'buildrequires': self.reg.re_buildrequires,
            # for prereq we append warning comment so we don't mess it there
            'requires': self.reg.re_requires,
            'recommends': self.reg.re_recommends,
            'suggests': self.reg.re_suggests,
            'supplements': self.reg.re_supplements,
            # for provides/obsoletes, we have a special case because we group them
            # for build root, we have a special match because we force its value
            'buildarch': self.reg.re_buildarch,
        }

        # deprecated matches that we no longer want to show up
        self.category_to_clean = {
            'vendor': self.reg.re_vendor,
            'autoreqprov': self.reg.re_autoreqprov,
            'epoch': self.reg.re_epoch,
        }


    def _start_paragraph(self):
        self.paragraph = {}
        for i in self.categories_order:
            self.paragraph[i] = []
        self.current_group = []


    def _add_group(self, group):
        """
        Actually store the lines from groups to resulting output
        """
        t = type(group)

        if t == str:
            Section.add(self, group)
        elif t == list:
            for subgroup in group:
                self._add_group(subgroup)
        else:
            raise RpmException('Unknown type of group in preamble: %s' % t)


    def _sort_helper_key(self, a):
        t = type(a)
        if t == str:
            key = a
        elif t == list:
            key = a[-1]
        else:
            raise RpmException('Unknown type during sort: %s' % t)

        # Special case is the category grouping where we have to get the number in
        # after the value
        if self.reg.re_patch.match(key):
            match = self.reg.re_patch.match(key)
            key = int(match.group(2))
        elif self.reg.re_source.match(key):
            match = self.reg.re_source.match(key)
            value = match.group(1)
            if value == '':
                value = '0'
            key = int(value)
        # Put pkgconfig()-style packages at the end of the list, after all
        # non-pkgconfig()-style packages
        elif key.find('pkgconfig(') != -1:
            key = '1'+key
        else:
            key = '0'+key
        return key


    def _end_paragraph(self):
        # sort based on category order
        for i in self.categories_order:
            # sort-out within the ordered groups based on the key
            if i in self.categories_with_sorted_package_tokens:
                self.paragraph[i].sort(key=self._sort_helper_key)
            # sort-out within the ordered groups based on the keyword
            if i in self.categories_with_sorted_keyword_tokens:
                self.paragraph[i].sort(key=self._sort_helper_key)
            for group in self.paragraph[i]:
                self._add_group(group)
        if self.current_group:
            # the current group was not added to any category. It's just some
            # random stuff that should be at the end anyway.
            self._add_group(self.current_group)

        self._start_paragraph()


    def _fix_license(self, value):
        # split using 'or', 'and' and parenthesis, ignore empty strings
        licenses = filter(lambda a: a != '', re.split('(\(|\)| and | or )', value))

        for (index, license) in enumerate(licenses):
            license = self.strip_useless_spaces(license)
            license = license.replace('ORlater','or later')
            license = license.replace('ORsim','or similar')
            if self.license_conversions.has_key(license):
                license = self.license_conversions[license]
            licenses[index] = license

        # create back new string with replaced licenses
        s = ' '.join(licenses).replace("( ","(").replace(" )",")")
        return s

    def _pkgname_to_pkgconfig(self, value):
        # we just want the pkgname if we have version string there
        # and for the pkgconfig deps we need to put the version into
        # the braces
        split = value.split()
        pkgname = value.split()[0]
        version = value.replace(pkgname,'')
        pkgconfig = []
        if not pkgname in self.pkgconfig_conversions:
            # first check if the pacakge is in the replacements
            return [ value ]
        else:
            # first split the pkgconfig data
            pkgconf_list = self.pkgconfig_conversions[pkgname].split()
            # then add each pkgconfig to the list
            #print pkgconf_list
            for j in pkgconf_list:
                pkgconfig.append('pkgconfig({0}){1}'.format(j, version))
        return pkgconfig


    def _fix_list_of_packages(self, value):
        if self.reg.re_requires_token.match(value):
            tokens = [ item[1] for item in self.reg.re_requires_token.findall(value) ]
            # first loop over all and do formatting as we can get more deps for one
            expanded = []
            for token in tokens:
                token = token.replace('%{version}-%{release}', '%{version}')
                # cleanup whitespace
                token = token.replace(' ','')
                # rpm actually allows ',' separated list of deps
                token = token.replace(',','')
                token = re.sub(r'([<>]=?|=)', r' \1 ', token)
                token = self._pkgname_to_pkgconfig(token)
                expanded += token
            # and then sort them :)
            expanded.sort()

            return expanded
        else:
            return [ value ]

    def _add_line_value_to(self, category, value, key = None):
        """
            Change a key-value line, to make sure we have the right spacing.

            Note: since we don't have a key <-> category matching, we need to
            redo one. (Eg: Provides and Obsoletes are in the same category)
        """
        keylen = len('BuildRequires:  ')

        if key:
            pass
        elif self.category_to_key.has_key(category):
            key = self.category_to_key[category]
        else:
            raise RpmException('Unhandled category in preamble: %s' % category)

        key += ':'
        # if the key is already longer then just add one space
        if len(key) >= keylen:
            key += ' '
        # fillup rest of the alignment if key is shorter than muster
        while len(key) < keylen:
            key += ' '

        if category in self.categories_with_package_tokens:
            values = self._fix_list_of_packages(value)
        else:
            values = [ value ]

        for value in values:
            line = key + value
            self._add_line_to(category, line)


    def _add_line_to(self, category, line):
        if self.current_group:
            self.current_group.append(line)
            self.paragraph[category].append(self.current_group)
            self.current_group = []
        else:
            self.paragraph[category].append(line)

        self.previous_line = line

    def _read_pkgconfig_changes(self):
        pkgconfig = {}

        files = FileUtils()
        files.open_datafile(PKGCONFIG_CONVERSIONS)
        for line in files.f:
            # the values are split by  ': '
            pair = line.split(': ')
            pkgconfig[pair[0]] = pair[1][:-1]
        files.close()
        return pkgconfig

    def _read_licenses_changes(self):
        licenses = {}

        files = FileUtils()
        f = files.open_datafile(LICENSES_CHANGES)
        # ignore first line containing 'First line' (WTF?)
        files.f.readline()
        # load and store the rest
        for line in files.f:
            # strip newline
            line = line[:-1]
            # file has format
            # correct license string<tab>known bad license string
            # tab is used as separator
            pair = line.split('\t')
            licenses[pair[1]] = pair[0]
        files.close()
        return licenses


    def add(self, line):
        line = self._complete_cleanup(line)
        # if the line is empty just skip it we don't need new section for it
        if len(line) == 0:
            return

        elif self.reg.re_if.match(line):
            # %if/%else/%endif marks the end of the previous paragraph
            # We append the line at the end of the previous paragraph, though,
            # since it will stay at the end there. If putting it at the
            # beginning of the next paragraph, it will likely move (with the
            # misc category).
            self.current_group.append(line)
            self._end_paragraph()
            self.previous_line = line
            return

        elif self.reg.re_comment.match(line):
            self.current_group.append(line)
            self.previous_line = line
            return

        elif self.reg.re_source.match(line):
            match = self.reg.re_source.match(line)
            self._add_line_value_to('source', match.group(2), key = 'Source%s' % match.group(1))
            return

        elif self.reg.re_patch.match(line):
            # FIXME: this is not perfect, but it's good enough for most cases
            if not self.previous_line or not self.reg.re_comment.match(self.previous_line):
                self.current_group.append('# PATCH-MISSING-TAG -- See http://wiki.opensuse.org/openSUSE:Packaging_Patches_guidelines')
                self.previous_line = line

            match = self.reg.re_patch.match(line)
            # convert Patch: to Patch0:
            if match.group(2) == '':
                zero = '0'
            else:
                zero = ''
            self._add_line_value_to('patch', match.group(3), key = '%sPatch%s%s' % (match.group(1), zero, match.group(2)))
            return

        elif self.reg.re_prereq.match(line):
            match = self.reg.re_prereq.match(line)
            # add the comment about using proper macro which needs investingaton
            self.current_group.append('# FIXME: use proper Requires(pre/post/preun/...)')
            self._add_line_value_to('prereq', match.group(1))
            return

        elif self.reg.re_requires_phase.match(line):
            match = self.reg.re_requires_phase.match(line)
            # Put the requires content properly as key for formatting
            self._add_line_value_to('prereq', match.group(2), key = 'Requires{0}'.format(match.group(1)))
            return

        elif self.reg.re_provides.match(line):
            match = self.re_provides.match(line)
            self._add_line_value_to('provides_obsoletes', match.group(1), key = 'Provides')
            return

        elif self.reg.re_obsoletes.match(line):
            match = self.re_obsoletes.match(line)
            self._add_line_value_to('provides_obsoletes', match.group(1), key = 'Obsoletes')
            return

        elif self.reg.re_buildroot.match(line):
            # we only are fine with buildroot only once
            if len(self.paragraph['buildroot']) == 0:
                self._add_line_value_to('buildroot', '%{_tmppath}/%{name}-%{version}-build')
            return

        elif self.reg.re_license.match(line):
            # first convert the license string to proper format and then append it
            match = self.reg.re_license.match(line)
            value = match.groups()[len(match.groups()) - 1]
            value = self._fix_license(value)
            self._add_line_value_to('license', value)
            return


        elif self.reg.re_release.match(line):
            # the release is always 0
            self._add_line_value_to('release', '0')
            return

        elif self.reg.re_summary_localized.match(line):
            match = self.reg.re_summary_localized.match(line)
            # we need to know what language we need
            language = match.group(1)
            # and what value is there
            content = match.group(2)
            self._add_line_value_to('summary_localized', content, key = 'Summary{0}'.format(language))
            return

        # loop for all other matching categories which
        # do not require special attention
        else:
            # cleanup
            for (category, regexp) in self.category_to_clean.iteritems():
                match = regexp.match(line)
                if match:
                    return

            # simple matching
            for (category, regexp) in self.category_to_re.iteritems():
                match = regexp.match(line)
                if match:
                    # instead of matching first group as there is only one,
                    # take the last group
                    # (so I can have more advanced regexp for RPM tags)
                    self._add_line_value_to(category, match.groups()[len(match.groups()) - 1])
                    return

            self._add_line_to('misc', line)


    def output(self, fout):
        self._end_paragraph()
        # append empty line to the end of the section
        self.lines.append('')
        Section.output(self, fout)


class RpmPackage(RpmPreamble):
    """
    We handle subpackage case as the normal preamble
    """


    def add(self, line):
        # The first line (%package) should always be added and is different
        # from the lines we handle in RpmPreamble.
        if self.previous_line is None:
            Section.add(self, line)
            return

        RpmPreamble.add(self, line)